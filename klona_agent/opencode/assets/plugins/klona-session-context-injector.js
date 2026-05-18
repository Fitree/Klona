import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

const INITIAL_CONTEXT_OPEN = "<Klona_memory_mental_model>\n"
const INITIAL_CONTEXT_CLOSE = "\n</Klona_memory_mental_model>"
const VAULT_SKILLS_OPEN = "<Klona_vault_skills>\n"
const VAULT_SKILLS_CLOSE = "\n</Klona_vault_skills>"
const DEFAULT_MCP_NAME = "klona_memory"
const DEFAULT_MCP_TIMEOUT_MS = 600_000
const KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH = "/KLONA_MEMORY_MENTAL_MODEL.md"
const INTERNAL_MENTAL_MODEL_PATH = "/internal/mental-model"
const INTERNAL_VAULT_SKILLS_PATH = "/internal/skills"
const PLUGIN_STATE_DIR = path.join(
  os.homedir(),
  ".local",
  "share",
  "opencode",
  "plugin-state",
  "klona-session-context-injector",
)
export const KlonaSessionContextInjectorPlugin = async ({ client }) => {
  let resolvedConfig
  const loggedSessions = new Set()

  async function log(level, message, extra = {}) {
    try {
      await client.app.log({
        body: {
          service: "klona-session-context-injector",
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
    return internalEndpointUrl(mcpUrl, INTERNAL_MENTAL_MODEL_PATH)
  }

  function vaultSkillsEndpointUrl(mcpUrl) {
    return internalEndpointUrl(mcpUrl, INTERNAL_VAULT_SKILLS_PATH)
  }

  function internalEndpointUrl(mcpUrl, internalPath) {
    const url = new URL(mcpUrl)
    const pathname = url.pathname.replace(/\/+$/, "")
    url.pathname = pathname.endsWith("/mcp")
      ? `${pathname.slice(0, -4)}${internalPath}`
      : internalPath
    url.search = ""
    url.hash = ""
    return url.toString()
  }

  async function fetchPrivateJsonEndpoint({ url, headers, timeout, label }) {
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
      if (raw.trim()) {
        try {
          payload = JSON.parse(raw)
        } catch (error) {
          if (response.status === 404) return { status: "missing" }
          throw error
        }
      }

      if (response.status === 404 && (payload?.status === "missing" || payload === null)) return { status: "missing" }
      if (!response.ok) throw new Error(`private ${label} endpoint failed (${response.status}): ${raw || response.statusText}`)
      return payload
    } finally {
      clearTimeout(timer)
    }
  }

  async function readKlonaMemoryMentalModel() {
    const config = getMemoryMcpConfig()
    if (!config) return { status: "missing", content: "" }

    const endpointUrl = mentalModelEndpointUrl(config.url)
    const payload = await fetchPrivateJsonEndpoint({
      url: endpointUrl,
      headers: config.headers,
      timeout: config.timeout,
      label: "mental-model",
    })

    if (payload?.status === "ok" && typeof payload.content === "string") {
      return { status: "ok", content: payload.content }
    }
    if (payload?.status === "missing") {
      return { status: "missing", content: "" }
    }
    throw new Error(payload?.error || "private mental-model endpoint returned an unexpected response")
  }

  async function readKlonaVaultSkillsCatalog() {
    const config = getMemoryMcpConfig()
    if (!config) return { status: "missing", content: "" }

    const endpointUrl = vaultSkillsEndpointUrl(config.url)
    const payload = await fetchPrivateJsonEndpoint({
      url: endpointUrl,
      headers: config.headers,
      timeout: config.timeout,
      label: "vault-skills",
    })

    if (payload?.status === "ok" && typeof payload.content === "string") {
      return { status: "ok", content: payload.content }
    }
    if (payload?.status === "ok" && Array.isArray(payload.skills)) {
      const content = formatVaultSkillsCatalog(payload.skills)
      return { status: content ? "ok" : "missing", content }
    }
    if (payload?.status === "missing") {
      return { status: "missing", content: "" }
    }
    throw new Error(payload?.error || "private vault-skills endpoint returned an unexpected response")
  }

  function sanitizeVaultSkillCatalogDescription(description) {
    return String(description)
      .replace(/[\s\x00-\x1f\x7f]+/g, " ")
      .replaceAll("<Klona_vault_skills>", "")
      .replaceAll("</Klona_vault_skills>", "")
      .trim()
      .slice(0, 300)
  }

  function formatVaultSkillsCatalog(skills) {
    return skills
      .filter((skill) => skill && typeof skill.name === "string" && typeof skill.description === "string")
      .map((skill) => ({ name: skill.name, description: sanitizeVaultSkillCatalogDescription(skill.description) }))
      .filter((skill) => skill.description)
      .map((skill) => `- ${skill.name}: ${skill.description}`)
      .join("\n")
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

  async function getSessionMessageHistoryStatus(sessionID) {
    try {
      const response = await client.session.messages({
        path: { id: sessionID },
        query: { limit: 1 },
      })

      if (response?.error) {
        await log("warn", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session messages lookup returned an error", {
          sessionID,
          error: response.error?.message ?? String(response.error),
        })
        return { hasExistingMessages: true, reason: "message-history-unverified" }
      }

      const messages = response?.data ?? response
      if (!Array.isArray(messages)) {
        await log("warn", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session messages lookup returned an unexpected shape", {
          sessionID,
        })
        return { hasExistingMessages: true, reason: "message-history-unverified" }
      }

      return {
        hasExistingMessages: messages.length > 0,
        reason: messages.length > 0 ? "resumed-existing-session" : "new-empty-session",
      }
    } catch (error) {
      await log("warn", "Skipping KLONA_MEMORY_MENTAL_MODEL.md injection because session messages lookup failed", {
        sessionID,
        error: error instanceof Error ? error.message : String(error),
      })
      return { hasExistingMessages: true, reason: "message-history-unverified" }
    }
  }

  async function ensureInjectionStatus(sessionID) {
    const status = await readInjectionStatus(sessionID)
    if (status) {
      if (status.should_inject === true && status.reason === "first-user-message") {
        const messageHistoryStatus = await getSessionMessageHistoryStatus(sessionID)
        if (messageHistoryStatus.hasExistingMessages) {
          await writeInjectionStatus(sessionID, {
            should_inject: false,
            reason: `stale-first-user-message-${messageHistoryStatus.reason}`,
          })
          return null
        }
      }

      return status
    }

    const messageHistoryStatus = await getSessionMessageHistoryStatus(sessionID)
    if (messageHistoryStatus.hasExistingMessages) {
      await writeInjectionStatus(sessionID, { should_inject: false, reason: messageHistoryStatus.reason })
      return null
    }

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

  function wrapKlonaVaultSkillsCatalogContent(catalogContent) {
    return `${VAULT_SKILLS_OPEN}Catalog only; not full skill instructions. Load full content before applying a relevant vault skill by using the high-level klona_memory MCP skill tools: call load_skill for the skill, and lazily call load_skill_resource with skill_name and path for referenced resources.\n\n${catalogContent}${VAULT_SKILLS_CLOSE}`
  }

  function buildKlonaInjectionPrefix(memoryContent, vaultSkillsContent) {
    const blocks = []
    if (memoryContent) blocks.push(wrapKlonaMemoryMentalModelContent(memoryContent))
    if (vaultSkillsContent) blocks.push(wrapKlonaVaultSkillsCatalogContent(vaultSkillsContent))
    return blocks.join("\n\n")
  }

  function prependKlonaContextToFirstTextPart(output, prefix) {
    const firstTextPart = output.parts.find(
      (part) => part?.type === "text" && typeof part.text === "string",
    )

    if (firstTextPart) {
      if (firstTextPart.text.startsWith(INITIAL_CONTEXT_OPEN)) return false

      firstTextPart.text = `${prefix}\n\n${firstTextPart.text}`
      firstTextPart.synthetic = false
      firstTextPart.metadata = {
        ...(firstTextPart.metadata ?? {}),
        plugin: "klona-session-context-injector",
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
        if (!status) return
        if (status.should_inject === false) return

        await writeInjectionStatus(sessionID, { should_inject: false, reason: `${status.reason}-consumed` })

        const memoryResult = await readKlonaMemoryMentalModel()
        let vaultSkillsResult = { status: "missing", content: "" }
        try {
          vaultSkillsResult = await readKlonaVaultSkillsCatalog()
        } catch (error) {
          await log("warn", "Skipping Klona vault skills catalog injection because the catalog endpoint is unavailable", {
            sessionID,
            agent,
            path: INTERNAL_VAULT_SKILLS_PATH,
            error: error instanceof Error ? error.message : String(error),
          })
        }
        const memoryContent = memoryResult.content
        const vaultSkillsContent = vaultSkillsResult.content
        const injectionPrefix = buildKlonaInjectionPrefix(memoryContent, vaultSkillsContent)

        if (!injectionPrefix) {
          if (!loggedSessions.has(sessionID)) {
            loggedSessions.add(sessionID)
            await log("debug", "Skipping KLONA context injection because KLONA_MEMORY_MENTAL_MODEL.md and vault skills catalog are missing or empty", {
              sessionID,
              agent,
              path: KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH,
              status: memoryResult.status,
              vaultSkillsStatus: vaultSkillsResult.status,
            })
          }
          return
        }

        injected = prependKlonaContextToFirstTextPart(output, injectionPrefix)
        if (!injected) return

        if (!loggedSessions.has(sessionID)) {
          loggedSessions.add(sessionID)
          await log("info", "Prepended KLONA context into the first user message", {
            sessionID,
            agent,
            path: KLONA_MEMORY_MENTAL_MODEL_VAULT_PATH,
            chars: memoryContent.length,
            vaultSkillsChars: vaultSkillsContent.length,
            statusPath: injectionStatusFilePath(sessionID),
          })
        }
      } catch (error) {
        if (!loggedSessions.has(sessionID)) {
          loggedSessions.add(sessionID)
          await log("warn", "Failed to inject KLONA session context into session user context", {
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
