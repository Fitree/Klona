#!/usr/bin/env node
const fs = require("node:fs")
const path = require("node:path")

const MCP_NAME = "klona_memory"
const INTERNAL_MENTAL_MODEL_PATH = "/internal/mental-model"
const TIMEOUT_MS = 10_000

function pluginRoot() {
  return process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, "..")
}

function readMcpConfig() {
  try {
    const raw = fs.readFileSync(path.join(pluginRoot(), ".mcp.json"), "utf8")
    const data = JSON.parse(raw)
    const entry = data?.mcpServers?.[MCP_NAME]
    if (!entry || entry.type !== "http" || !entry.url) return null
    return entry
  } catch {
    return null
  }
}

function readInstructions() {
  try {
    return fs.readFileSync(path.join(pluginRoot(), "instructions", "klona-memory.md"), "utf8").trim()
  } catch {
    return `# KLONA Memory\n\nUse the configured \`${MCP_NAME}\` MCP tools for all memory operations.`
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

async function fetchMentalModel(entry) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    const response = await fetch(mentalModelEndpointUrl(entry.url), {
      method: "GET",
      headers: { Accept: "application/json", ...(entry.headers || {}) },
      signal: controller.signal,
    })
    const raw = await response.text()
    const payload = raw.trim() ? JSON.parse(raw) : null
    if (response.status === 404 && payload?.status === "missing") return ""
    if (!response.ok) return ""
    return payload?.status === "ok" && typeof payload.content === "string" ? payload.content : ""
  } catch {
    return ""
  } finally {
    clearTimeout(timer)
  }
}

function emit(additionalContext) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext,
    },
  }))
}

async function main() {
  const entry = readMcpConfig()
  const mentalModel = entry ? await fetchMentalModel(entry) : ""
  const workflowContext = readInstructions()
  const parts = [workflowContext]
  if (mentalModel) {
    parts.push(`<Klona_memory_mental_model>\n${mentalModel}\n</Klona_memory_mental_model>`)
  }
  emit(parts.join("\n\n"))
}

main().catch(() => emit(readInstructions()))
