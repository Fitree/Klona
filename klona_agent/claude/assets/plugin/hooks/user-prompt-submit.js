#!/usr/bin/env node
const NUDGE = "Klona memory nudge: decide whether to call `klona_memory.recall(input)` before answering and whether to silently call `klona_memory.remember(input)` after processing durable user/project context."

process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: NUDGE,
  },
}))
