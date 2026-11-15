package io.github.ninjatuna007.henchline

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * The call history (the user's wish, 2026-11-16): each ring (who rang, why, answered / declined / missed) and each
 * call (when, how long), newest last, one JSON line each, the newest KEEP of them.
 */
object Calls {
    private const val KEEP = 200

    private fun file(context: Context) = File(context.filesDir, "calls.jsonl")

    /** A ring's id: its one-time reply address, which every place that ends the ring knows. */
    fun ringId(reply: String?) = reply?.substringAfterLast('/')?.takeIf { it.isNotEmpty() }

    @Synchronized
    fun add(context: Context, entry: JSONObject) {
        val calls = file(context)
        val lines = (if (calls.exists()) calls.readLines().filter { it.isNotBlank() } else emptyList()) +
            entry.put("at", entry.optLong("at", System.currentTimeMillis())).toString()
        write(calls, lines.takeLast(KEEP))
    }

    /** Changes the entry with that id (a ring's outcome, a call's length); nothing when there is none. */
    @Synchronized
    fun update(context: Context, id: String?, change: (JSONObject) -> Unit) {
        val calls = file(context)
        if (id.isNullOrEmpty() || !calls.exists()) return
        write(calls, calls.readLines().filter { it.isNotBlank() }.map { raw ->
            try { JSONObject(raw).also { if (it.optString("id") == id) change(it) }.toString() } catch (_: Exception) { raw }
        })
    }

    /** How a ring ended, once: the first answer, decline or miss stands. */
    fun outcome(context: Context, reply: String?, what: String) = update(context, ringId(reply)) {
        // Answered but the laptop out of reach: "unreachable" says more than "answered"
        if (it.optString("outcome") == "ringing" || (what == "unreachable" && it.optString("outcome") == "answered"))
            it.put("outcome", what)
    }

    /** "45 s", "4 min", "1 h 12 min" */
    fun length(seconds: Long): String = when {
        seconds < 60 -> "$seconds s"
        seconds < 3600 -> "${seconds / 60} min"
        else -> "${seconds / 3600} h ${seconds % 3600 / 60} min"
    }

    @Synchronized
    fun load(context: Context): List<JSONObject> {
        val calls = file(context)
        if (!calls.exists()) return emptyList()
        return calls.readLines().mapNotNull { try { JSONObject(it) } catch (_: Exception) { null } }
    }

    private fun write(calls: File, lines: List<String>) {  // aside, then swapped in: a crash mid-write keeps the old
        val aside = File(calls.path + ".new")
        aside.writeText(lines.joinToString("\n", postfix = "\n"))
        aside.renameTo(calls)
    }
}
