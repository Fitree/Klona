import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

const MCP_PROTOCOL_VERSION = "2025-03-26"
const MCP_ACCEPT_HEADER = "application/json, text/event-stream"
const INITIAL_CONTEXT_OPEN = "<Klona_memory_mental_model>\n"
const INITIAL_CONTEXT_CLOSE = "\n</Klona_memory_mental_model>"
const DEFAULT_MCP_NAME = "klona_memory"
const DEFAULT_MCP_TIMEOUT_MS = 600_000
const KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH = "/KLONA_MEMORY_MENTAL_MODEL.md"
const PLUGIN_STATE_DIR = path.join(
  os.homedir(),
  ".local",
  "share",
  "opencode",
  "plugin-state",
  "klona-memory-mental-model-injector",
)
export const KlonaMemoryMentalModelInjectorPlugin = async ({ client }) => {
  let resolvedConfig
  const loggedSessions = new Set()

  async function log(level, message, extra = {}) {
    try {
      await client.app.log({
        body: {
          service: "klona-memory-mental-model-injector",
          level,
          message,
          extra,
        },
      })
    } catch {
      // Logging must never break the user session.
    }
  }

  function getMemoryMcpConfig() {
    const mcp = resolvedConfig?.mcp?.[DEFAULT_MCP_NAME]
    if (!mcp || mcp.type !== "remote" || mcp.enabled === false || !mcp.url) {
      return null
    }
    return {
      url: mcp.url,
      headers: mcp.headers ?? {},
      timeout: typeof mcp.timeout === "number" ? mcp.timeout : DEFAULT_MCP_TIMEOUT_MS,
    }
  }

  async function isRootSession(sessionID) {
    try {
      const response = await client.session.get({ path: { id: sessionID } })

      if (response?.error) {
        await log("warn", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session lookup returned an error", {
          sessionID,
          error: response.error?.message ?? String(response.error),
        })
        return false
      }

      const session = response?.data ?? response
      if (!session || session.id !== sessionID) {
        await log("warn", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session lookup returned an unexpected shape", {
          sessionID,
        })
        return false
      }

      return !session.parentID
    } catch (error) {
      await log("warn", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session lookup failed", {
        sessionID,
        error: error instanceof Error ? error.message : String(error),
      })
      return false
    }
  }


  async function parseMcpHttpResponse(response) {
    const contentType = response.headers.get("content-type") ?? ""
    const raw = await response.text()

    if (!response.ok) {
      throw new Error(`MCP request failed (${response.status}): ${raw || response.statusText}`)
    }

    if (!raw.trim()) return null

    if (contentType.includes("text/event-stream")) {
      const events = raw
        .split(/\r?\n\r?\n/)
        .map((chunk) => chunk
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n"))
        .filter(Boolean)

      for (const event of events) {
        const parsed = JSON.parse(event)
        if (parsed?.id !== undefined || parsed?.result || parsed?.error) return parsed
      }

      return null
    }

    return JSON.parse(raw)
  }

  async function mcpPost({ url, headers, sessionId, body, timeout }) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeout)

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          Accept: MCP_ACCEPT_HEADER,
          "Content-Type": "application/json",
          ...(sessionId ? { "Mcp-Session-Id": sessionId } : {}),
          ...headers,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      const payload = await parseMcpHttpResponse(response)
      return {
        payload,
        sessionId: response.headers.get("Mcp-Session-Id") ?? sessionId ?? undefined,
      }
    } finally {
      clearTimeout(timer)
    }
  }

  async function initializeMcp(config) {
    const initialize = await mcpPost({
      url: config.url,
      headers: config.headers,
      timeout: config.timeout,
      body: {
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocolVersion: MCP_PROTOCOL_VERSION,
          capabilities: {},
          clientInfo: {
            name: "klona-memory-mental-model-injector-plugin",
            version: "1.0.0",
          },
        },
      },
    })

    if (initialize.payload?.error) {
      throw new Error(initialize.payload.error.message || "MCP initialize failed")
    }

    const sessionId = initialize.sessionId
    await mcpPost({
      url: config.url,
      headers: config.headers,
      timeout: config.timeout,
      sessionId,
      body: {
        jsonrpc: "2.0",
        method: "notifications/initialized",
      },
    })

    return sessionId
  }

  function extractMemoryContent(result) {
    function extract(value) {
      if (!value) return ""
      if (typeof value === "string") return value.trim()

      if (typeof value === "object") {
        if (typeof value.result === "string") return value.result.trim()
        if (typeof value.content === "string") return value.content.trim()

        const structured = extract(value.structuredContent)
        if (structured) return structured

        const nestedResult = value.result && typeof value.result === "object" ? extract(value.result) : ""
        if (nestedResult) return nestedResult
      }

      return ""
    }

    const direct = extract(result?.structuredContent) || extract(result?.result) || extract(result?.content)
    if (direct) return direct

    const items = Array.isArray(result?.content) ? result.content : []
    for (const item of items) {
      if (item?.type !== "text" || typeof item.text !== "string") continue
      const text = item.text.trim()
      if (!text) continue

      try {
        const parsed = JSON.parse(text)
        const parsedContent = extract(parsed)
        if (parsedContent) return parsedContent
      } catch {
        return text
      }
    }

    return ""
  }

  function resultText(result) {
    const texts = []

    if (typeof result?.content === "string") texts.push(result.content)
    if (typeof result?.structuredContent?.content === "string") texts.push(result.structuredContent.content)

    const items = Array.isArray(result?.content) ? result.content : []
    for (const item of items) {
      if (item?.type === "text" && typeof item.text === "string") texts.push(item.text)
    }

    return texts.join("\n").toLowerCase()
  }

  function isMissingKlonaMemoryMentalModelResult(result) {
    if (!result?.isError) return false
    const text = resultText(result)
    return text.includes("not found") || text.includes("does not exist") || text.includes("no such file")
  }

  async function readKlonaMemoryMentalModel() {
    const config = getMemoryMcpConfig()
    if (!config) return ""

    const sessionId = await initializeMcp(config)
    const call = await mcpPost({
      url: config.url,
      headers: config.headers,
      timeout: config.timeout,
      sessionId,
      body: {
        jsonrpc: "2.0",
        id: 2,
        method: "tools/call",
        params: {
          name: "recall",
          arguments: {
            input: `Return the exact current content of ${KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH} if it exists. If it does not exist or is empty, return an empty result.`,
          },
        },
      },
    })

    if (call.payload?.error) {
      throw new Error(call.payload.error.message || `MCP tool call failed for ${KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH}`)
    }

    if (isMissingKlonaMemoryMentalModelResult(call.payload?.result)) {
      return ""
    }

    return extractMemoryContent(call.payload?.result)
  }

  function injectionStatusFilePath(sessionID) {
    return path.join(PLUGIN_STATE_DIR, `${sessionID}.json`)
  }

  async function readInjectionStatus(sessionID) {
    try {
      const raw = await fs.readFile(injectionStatusFilePath(sessionID), "utf8")
      return JSON.parse(raw)
    } catch (error) {
      if (error?.code === "ENOENT") return null
      throw error
    }
  }

  async function writeInjectionStatus(sessionID, status) {
    await fs.mkdir(PLUGIN_STATE_DIR, { recursive: true })
    await fs.writeFile(
      injectionStatusFilePath(sessionID),
      JSON.stringify(status, null, 2),
    )
  }

  async function ensureInjectionStatus(sessionID) {
    const status = await readInjectionStatus(sessionID)
    if (status) return status

    const initialStatus = { should_inject: true, reason: "first-user-message" }
    await writeInjectionStatus(sessionID, initialStatus)
    return initialStatus
  }

  async function markSessionNeedsInjection(sessionID) {
    try {
      await writeInjectionStatus(sessionID, { should_inject: true, reason: "post-compaction" })
    } catch (error) {
      await log("warn", "Failed to mark session for post-compaction KLONA_MEMORY_MENTAL_MODEL.md injection", {
        sessionID,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }

  function compactedSessionID(event) {
    const candidates = [
      event?.event?.sessionID,
      event?.event?.sessionId,
      event?.event?.session?.id,
      event?.event?.properties?.sessionID,
      event?.event?.properties?.sessionId,
      event?.event?.payload?.sessionID,
      event?.event?.payload?.sessionId,
      event?.sessionID,
      event?.sessionId,
      event?.session?.id,
      event?.properties?.sessionID,
      event?.properties?.sessionId,
      event?.payload?.sessionID,
      event?.payload?.sessionId,
    ]
    return candidates.find((value) => typeof value === "string" && value.length > 0)
  }

  function wrapKlonaMemoryMentalModelContent(memoryContent) {
    return `${INITIAL_CONTEXT_OPEN}${memoryContent}${INITIAL_CONTEXT_CLOSE}`
  }

  function prependKlonaMemoryMentalModelToFirstTextPart(output, memoryContent) {
    const prefix = wrapKlonaMemoryMentalModelContent(memoryContent)
    const firstTextPart = output.parts.find(
      (part) => part?.type === "text" && typeof part.text === "string",
    )

    if (firstTextPart) {
      if (firstTextPart.text.startsWith(INITIAL_CONTEXT_OPEN)) return false

      firstTextPart.text = `${prefix}\n\n${firstTextPart.text}`
      firstTextPart.synthetic = false
      firstTextPart.metadata = {
        ...(firstTextPart.metadata ?? {}),
        plugin: "klona-memory-mental-model-injector",
        prepended: true,
      }
      return true
    }

    return false
  }

  return {
    config: async (config) => {
      resolvedConfig = config
    },

    event: async (event) => {
      if (!(event?.event?.type === "session.compacted" || event?.type === "session.compacted")) return

      const sessionID = compactedSessionID(event)
      if (!sessionID) return

      await markSessionNeedsInjection(sessionID)
    },

    "chat.message": async ({ sessionID, agent }, output) => {
      if (!sessionID) return
      if (!(await isRootSession(sessionID))) return

      let injected = false

      try {
        const status = await ensureInjectionStatus(sessionID)
        if (status.should_inject === false) return

        const memoryContent = await readKlonaMemoryMentalModel()

        if (!memoryContent) {
          if (!loggedSessions.has(sessionID)) {
            loggedSessions.add(sessionID)
            await log("debug", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because the file is missing or empty", {
              sessionID,
              agent,
              path: KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH,
            })
          }
          return
        }

        injected = prependKlonaMemoryMentalModelToFirstTextPart(output, memoryContent)
        if (!injected) return

        await writeInjectionStatus(sessionID, { should_inject: false })

        if (!loggedSessions.has(sessionID)) {
          loggedSessions.add(sessionID)
          await log("info", "Prepended KLONA_MEMORY_MENTAL_MODEL.md into the first user message", {
            sessionID,
            agent,
            path: KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH,
            chars: memoryContent.length,
            statusPath: injectionStatusFilePath(sessionID),
          })
        }
      } catch (error) {
        if (!loggedSessions.has(sessionID)) {
          loggedSessions.add(sessionID)
          await log("warn", "Failed to inject KLONA_MEMORY_MENTAL_MODEL.md into session user context", {
            sessionID,
            agent,
            path: KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH,
            error: error instanceof Error ? error.message : String(error),
          })
        }
      }
    },
  }
}
