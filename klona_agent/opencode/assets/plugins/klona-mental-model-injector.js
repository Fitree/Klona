import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

const MCP_PROTOCOL_VERSION = "2025-03-26"
const MCP_ACCEPT_HEADER = "application/json, text/event-stream"
const INITIAL_CONTEXT_OPEN = "<Mental_model>\n"
const INITIAL_CONTEXT_CLOSE = "\n</Mental_model>"
const DEFAULT_MCP_NAME = "klona_memory_server"
const MENTAL_MODEL_FILE_PATH = "/MENTAL_MODEL.md"
const MENTAL_MODEL_MARKER = "mental-model-v1"
const PLUGIN_STATE_DIR = path.join(
  process.env.XDG_DATA_HOME ?? path.join(os.homedir(), ".local", "share"),
  "opencode",
  "plugin-state",
  "klona-mental-model-injector",
)
const LEGACY_PLUGIN_STATE_DIR = path.join(
  process.env.XDG_DATA_HOME ?? path.join(os.homedir(), ".local", "share"),
  "opencode",
  "plugin-state",
  "klona-memory-session",
)

export const KlonaMentalModelInjectorPlugin = async ({ client }) => {
  let resolvedConfig
  const loggedSessions = new Set()

  async function log(level, message, extra = {}) {
    try {
      await client.app.log({
        body: {
          service: "klona-mental-model-injector",
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
      timeout: typeof mcp.timeout === "number" ? mcp.timeout : 5000,
    }
  }

  async function isRootSession(sessionID) {
    try {
      const response = await client.session.get({ path: { id: sessionID } })

      if (response?.error) {
        await log("warn", "Skipping MENTAL_MODEL.md injection because session lookup returned an error", {
          sessionID,
          error: response.error?.message ?? String(response.error),
        })
        return false
      }

      const session = response?.data ?? response
      if (!session || session.id !== sessionID) {
        await log("warn", "Skipping MENTAL_MODEL.md injection because session lookup returned an unexpected shape", {
          sessionID,
        })
        return false
      }

      return !session.parentID
    } catch (error) {
      await log("warn", "Skipping MENTAL_MODEL.md injection because session lookup failed", {
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
            name: "klona-mental-model-injector-plugin",
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
    const direct = result?.structuredContent ?? result?.content
    if (direct && typeof direct === "object" && typeof direct.content === "string") {
      return direct.content.trim()
    }

    const items = Array.isArray(result?.content) ? result.content : []
    for (const item of items) {
      if (item?.type !== "text" || typeof item.text !== "string") continue
      const text = item.text.trim()
      if (!text) continue

      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed.content === "string") {
          return parsed.content.trim()
        }
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

  function isMissingMentalModelResult(result) {
    if (!result?.isError) return false
    const text = resultText(result)
    return text.includes("not found") || text.includes("does not exist") || text.includes("no such file")
  }

  async function readMentalModel() {
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
          name: "vault_read",
          arguments: {
            path: MENTAL_MODEL_FILE_PATH,
          },
        },
      },
    })

    if (call.payload?.error) {
      throw new Error(call.payload.error.message || `MCP tool call failed for ${MENTAL_MODEL_FILE_PATH}`)
    }

    if (isMissingMentalModelResult(call.payload?.result)) {
      return ""
    }

    return extractMemoryContent(call.payload?.result)
  }

  function markerFilePath(sessionID) {
    return path.join(PLUGIN_STATE_DIR, `${sessionID}.json`)
  }

  function legacyMarkerFilePath(sessionID) {
    return path.join(LEGACY_PLUGIN_STATE_DIR, `${sessionID}.json`)
  }

  function postCompactionMarkerFilePath(sessionID) {
    return path.join(PLUGIN_STATE_DIR, `${sessionID}.post-compaction.json`)
  }

  async function markerExists(filePath) {
    try {
      await fs.access(filePath)
      return true
    } catch (error) {
      if (error?.code === "ENOENT") return false
      throw error
    }
  }

  async function claimInjectedSessionMarker(sessionID) {
    try {
      if (await markerExists(legacyMarkerFilePath(sessionID))) return false
      await fs.mkdir(PLUGIN_STATE_DIR, { recursive: true })
      await fs.writeFile(
        markerFilePath(sessionID),
        JSON.stringify({
          sessionID,
          marker: MENTAL_MODEL_MARKER,
          path: MENTAL_MODEL_FILE_PATH,
          time: new Date().toISOString(),
        }, null, 2),
        { flag: "wx" },
      )
      return true
    } catch (error) {
      if (error?.code === "EEXIST") return false
      throw error
    }
  }

  async function ensureInjectedSessionMarker(sessionID) {
    try {
      await fs.mkdir(PLUGIN_STATE_DIR, { recursive: true })
      await fs.writeFile(
        markerFilePath(sessionID),
        JSON.stringify({
          sessionID,
          marker: MENTAL_MODEL_MARKER,
          path: MENTAL_MODEL_FILE_PATH,
          time: new Date().toISOString(),
        }, null, 2),
        { flag: "wx" },
      )
    } catch (error) {
      if (error?.code === "EEXIST") return
      throw error
    }
  }

  async function removeInjectedSessionMarker(sessionID) {
    try {
      await fs.unlink(markerFilePath(sessionID))
    } catch {
      // Best-effort cleanup only.
    }
  }

  async function markSessionNeedsPostCompactionInjection(sessionID) {
    try {
      await fs.mkdir(PLUGIN_STATE_DIR, { recursive: true })
      await fs.writeFile(
        postCompactionMarkerFilePath(sessionID),
        JSON.stringify({
          sessionID,
          marker: MENTAL_MODEL_MARKER,
          reason: "post-compaction",
          path: MENTAL_MODEL_FILE_PATH,
          time: new Date().toISOString(),
        }, null, 2),
      )
    } catch (error) {
      await log("warn", "Failed to mark session for post-compaction MENTAL_MODEL.md injection", {
        sessionID,
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }

  async function hasPostCompactionInjectionMarker(sessionID) {
    try {
      return await markerExists(postCompactionMarkerFilePath(sessionID))
    } catch (error) {
      await log("warn", "Failed to check post-compaction MENTAL_MODEL.md injection marker", {
        sessionID,
        error: error instanceof Error ? error.message : String(error),
      })
      return false
    }
  }

  async function removePostCompactionInjectionMarker(sessionID) {
    try {
      await fs.unlink(postCompactionMarkerFilePath(sessionID))
    } catch (error) {
      if (error?.code === "ENOENT") return
      await log("warn", "Failed to remove post-compaction MENTAL_MODEL.md injection marker", {
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

  function wrapMentalModelContent(memoryContent) {
    return `${INITIAL_CONTEXT_OPEN}${memoryContent}${INITIAL_CONTEXT_CLOSE}`
  }

  function prependMentalModelToFirstTextPart(output, memoryContent) {
    const prefix = wrapMentalModelContent(memoryContent)
    const firstTextPart = output.parts.find(
      (part) => part?.type === "text" && typeof part.text === "string",
    )

    if (firstTextPart) {
      if (firstTextPart.text.startsWith(INITIAL_CONTEXT_OPEN)) return false

      firstTextPart.text = `${prefix}\n\n${firstTextPart.text}`
      firstTextPart.synthetic = false
      firstTextPart.metadata = {
        ...(firstTextPart.metadata ?? {}),
        plugin: "klona-mental-model-injector",
        marker: MENTAL_MODEL_MARKER,
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

      await markSessionNeedsPostCompactionInjection(sessionID)
    },

    "chat.message": async ({ sessionID, agent }, output) => {
      if (!sessionID) return
      if (!(await isRootSession(sessionID))) return

      let claimedMarker = false
      let injected = false

      try {
        const shouldInjectAfterCompaction = await hasPostCompactionInjectionMarker(sessionID)
        if (!shouldInjectAfterCompaction) claimedMarker = await claimInjectedSessionMarker(sessionID)
        if (!claimedMarker && !shouldInjectAfterCompaction) return

        const memoryContent = await readMentalModel()

        if (!memoryContent) {
          if (claimedMarker) await removeInjectedSessionMarker(sessionID)
          claimedMarker = false

          if (!loggedSessions.has(sessionID)) {
            loggedSessions.add(sessionID)
            await log("debug", "Skipping MENTAL_MODEL.md injection because the file is missing or empty", {
              sessionID,
              agent,
              path: MENTAL_MODEL_FILE_PATH,
            })
          }
          return
        }

        injected = prependMentalModelToFirstTextPart(output, memoryContent)
        if (!injected) {
          if (claimedMarker) await removeInjectedSessionMarker(sessionID)
          claimedMarker = false
          return
        }

        if (shouldInjectAfterCompaction) {
          await ensureInjectedSessionMarker(sessionID)
          await removePostCompactionInjectionMarker(sessionID)
        }

        if (!loggedSessions.has(sessionID)) {
          loggedSessions.add(sessionID)
          await log("info", "Prepended MENTAL_MODEL.md into the first user message", {
            sessionID,
            agent,
            path: MENTAL_MODEL_FILE_PATH,
            chars: memoryContent.length,
            markerPath: markerFilePath(sessionID),
          })
        }
      } catch (error) {
        if (claimedMarker && !injected) {
          await removeInjectedSessionMarker(sessionID)
        }

        if (!loggedSessions.has(sessionID)) {
          loggedSessions.add(sessionID)
          await log("warn", "Failed to inject MENTAL_MODEL.md into session user context", {
            sessionID,
            agent,
            path: MENTAL_MODEL_FILE_PATH,
            error: error instanceof Error ? error.message : String(error),
          })
        }
      }
    },
  }
}
