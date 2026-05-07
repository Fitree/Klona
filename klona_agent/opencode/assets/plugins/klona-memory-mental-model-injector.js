import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

const INITIAL_CONTEXT_OPEN = "<Klona_memory_mental_model>\n"
const INITIAL_CONTEXT_CLOSE = "\n</Klona_memory_mental_model>"
const DEFAULT_MCP_NAME = "klona_memory"
const DEFAULT_MCP_TIMEOUT_MS = 600_000
const KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH = "/KLONA_MEMORY_MENTAL_MODEL.md"
const INTERNAL_MENTAL_MODEL_PATH = "/internal/mental-model"
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

  function mentalModelEndpointUrl(mcpUrl) {
    const url = new URL(mcpUrl)
    const pathname = url.pathname.replace(/\/+$/, "")
    url.pathname = pathname.endsWith("/mcp")
      ? `${pathname.slice(0, -4)}${INTERNAL_MENTAL_MODEL_PATH}`
      : INTERNAL_MENTAL_MODEL_PATH
    url.search = ""
    url.hash = ""
    return url.toString()
  }

  async function fetchPrivateMentalModelEndpoint({ url, headers, timeout }) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeout)

    try {
      const response = await fetch(url, {
        method: "GET",
        headers: {
          Accept: "application/json",
          ...headers,
        },
        signal: controller.signal,
      })

      const raw = await response.text()
      let payload = null
      if (raw.trim()) payload = JSON.parse(raw)

      if (response.status === 404 && payload?.status === "missing") return payload
      if (!response.ok) throw new Error(`private mental-model endpoint failed (${response.status}): ${raw || response.statusText}`)
      return payload
    } finally {
      clearTimeout(timer)
    }
  }

  async function readKlonaMemoryMentalModel() {
    const config = getMemoryMcpConfig()
    if (!config) return { status: "missing", content: "" }

    const endpointUrl = mentalModelEndpointUrl(config.url)
    const payload = await fetchPrivateMentalModelEndpoint({
      url: endpointUrl,
      headers: config.headers,
      timeout: config.timeout,
    })

    if (payload?.status === "ok" && typeof payload.content === "string") {
      return { status: "ok", content: payload.content }
    }
    if (payload?.status === "missing") {
      return { status: "missing", content: "" }
    }
    throw new Error(payload?.error || "private mental-model endpoint returned an unexpected response")
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

        const memoryResult = await readKlonaMemoryMentalModel()
        const memoryContent = memoryResult.content

        if (!memoryContent) {
          await writeInjectionStatus(sessionID, { should_inject: false })
          if (!loggedSessions.has(sessionID)) {
            loggedSessions.add(sessionID)
            await log("debug", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because the file is missing or empty", {
              sessionID,
              agent,
              path: KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH,
              status: memoryResult.status,
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
