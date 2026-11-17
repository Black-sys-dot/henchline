package io.github.ninjatuna007.henchline

import android.content.Context
import org.json.JSONObject
import java.io.File

/**
 * Each agent's chat, kept on the phone (the user's wish, 2026-11-15: like a messaging app's): one file per
 * agent session, a JSON line per message, the newest KEEP of them. Readable off a call too. Notes about the
 * call itself are not kept; your attachments are kept by name (the app that lent them may take them back).
 */
object Chats {
    private const val KEEP = 400
    private const val PRUNE_DAYS = 90

    private fun file(context: Context, session: String) =
        File(context.filesDir, "chats/" + session.replace(Regex("[^A-Za-z0-9._-]"), "_") + ".jsonl")

    private val lastAt = HashMap<String, Long>()  // each chat's newest "at" given out
    private val lasts = HashMap<String, JSONObject?>()  // each chat's newest line, for the list: no file re-read

    /** A chat's newest line (the list's preview), read from its file once and then kept up to date. */
    @Synchronized
    fun last(context: Context, session: String): JSONObject? = lasts.getOrPut(session) {
        val chat = file(context, session)
        if (session.isEmpty() || !chat.exists()) null
        else chat.readLines().lastOrNull { it.isNotBlank() }?.let { try { JSONObject(it) } catch (_: Exception) { null } }
    }

    @Synchronized
    fun add(context: Context, session: String, line: JSONObject) {
        if (session.isEmpty()) return
        lasts[session] = line
        val chat = file(context, session)
        chat.parentFile?.mkdirs()
        // Never two lines at the same millisecond (a text and its file kept together): "at" names a line to delete
        val at = maxOf(System.currentTimeMillis(), (lastAt[session] ?: 0L) + 1).also { lastAt[session] = it }
        chat.appendText(line.put("at", at).toString() + "\n")
        if (chat.length() > 512 * 1024) {  // now and then, the oldest go
            val lines = chat.readLines()
            if (lines.size > KEEP) {  // written aside, then swapped in: a crash mid-write keeps the old chat
                val trimmed = File(chat.path + ".new")
                trimmed.writeText(lines.takeLast(KEEP).joinToString("\n", postfix = "\n"))
                trimmed.renameTo(chat)
            }
            // Chats of agent sessions untouched for PRUNE_DAYS go
            chat.parentFile?.listFiles()?.filter { it.isFile && System.currentTimeMillis() - it.lastModified() > PRUNE_DAYS * 86_400_000L }
                ?.forEach { it.delete() }
            lasts.clear()  // trimmed or pruned: the list reads them again
            sweep(context)
        }
    }

    /** Pictures' small copies that no kept line names any more (trimmed away, or its chat pruned) go too. */
    private fun sweep(context: Context) {
        val folder = File(context.filesDir, "chats")
        val named = folder.listFiles { f -> f.isFile && f.name.endsWith(".jsonl") }.orEmpty()
            .flatMap { it.readLines() }.mapNotNull { try { JSONObject(it).optString("thumb").ifEmpty { null } } catch (_: Exception) { null } }
            .toSet()
        val settled = System.currentTimeMillis() - 10 * 60_000L  // newer: its line may not be written yet
        File(folder, "thumbs").listFiles()?.filter { it.path !in named && it.lastModified() < settled }?.forEach { it.delete() }
    }

    /** Your message's ticks, a messaging app's way: "sent" (one: the laptop has it), "delivered" (two: typed into the
     *  agent), "read" (two, blue: the agent has answered since). */
    @Synchronized
    fun setStatus(context: Context, session: String, id: String, status: String, force: Boolean = false,
                  takenAt: Long = 0): String {
        // Taken in at takenAt (ms), and the agent has answered since: its reply reached the phone before word that it
        // took this in (2026-11-17), so straight to blue, as readAll would have made it. Only lines after takenAt:
        // an answer to an earlier message, while this one waited in the queue, isn't one to this
        var mine = false
        var answered = false
        if (status == "delivered" && takenAt > 0) for (line in load(context, session)) {
            if (line.optString("id") == id) mine = true
            else if (mine && !line.optBoolean("mine") && !line.optBoolean("note") && line.optString("from").isNotEmpty()
                && line.optLong("at") >= takenAt) answered = true
        }
        val wanted = if (answered) "read" else status
        var now = wanted
        rewrite(context, session) { line ->  // force: back to "waiting" (the laptop couldn't be reached after all)
            if (line.optString("id") != id) line
            else if (force || rank(wanted) > rank(line.optString("status"))) line.put("status", wanted)
            else line.also { now = it.optString("status") }
        }
        return now
    }

    /** Every chat kept on the phone, by session: one an agent texted that the crew list doesn't name still shows
     *  (a Cursor text reached the notification but no chat, 2026-11-16). */
    @Synchronized
    fun sessions(context: Context): List<String> =
        File(context.filesDir, "chats").listFiles { f -> f.isFile && f.name.endsWith(".jsonl") }
            ?.map { it.name.removeSuffix(".jsonl") } ?: emptyList()

    /** The chat emptied, on this phone only (the long-press menu): the laptop and the agent keep theirs. */
    @Synchronized
    fun clear(context: Context, session: String) {
        if (session.isEmpty()) return
        file(context, session).delete()
        lasts.remove(session)
        sweep(context)
    }

    /** A chat moved to the agent's own key (it was found running before it spoke, under another): its lines join
     *  any already there, in time order. Whether there was anything to move. */
    @Synchronized
    fun merge(context: Context, old: String, new: String): Boolean {
        val from = file(context, old)
        if (old.isEmpty() || new.isEmpty() || old == new || !from.exists()) return false
        val to = file(context, new)
        val lines = (from.readLines() + (if (to.exists()) to.readLines() else emptyList())).filter { it.isNotBlank() }
            .sortedBy { try { JSONObject(it).optLong("at") } catch (_: Exception) { 0L } }
        val aside = File(to.path + ".new")
        aside.writeText(lines.joinToString("\n", postfix = "\n"))
        aside.renameTo(to)
        from.delete()
        lasts.remove(old); lasts.remove(new)
        lastAt[new] = maxOf(lastAt[new] ?: 0L, lastAt.remove(old) ?: 0L)
        return true
    }

    /** Lines deleted on this phone ("Delete for me"), by when they were added: the removed ones. */
    @Synchronized
    fun remove(context: Context, session: String, ats: Set<Long>): List<JSONObject> {
        val chat = file(context, session)
        if (session.isEmpty() || !chat.exists() || ats.isEmpty()) return emptyList()
        val removed = ArrayList<JSONObject>()
        val kept = chat.readLines().filter { raw ->
            val line = try { JSONObject(raw) } catch (_: Exception) { null }
            if (line != null && line.optLong("at", -1) in ats) { removed.add(line); false } else true
        }
        val aside = File(chat.path + ".new")
        aside.writeText(if (kept.isEmpty()) "" else kept.joinToString("\n", postfix = "\n"))
        aside.renameTo(chat)
        lasts[session] = kept.lastOrNull { it.isNotBlank() }?.let { try { JSONObject(it) } catch (_: Exception) { null } }
        sweep(context)
        return removed
    }

    /** A line's words changed in place: a voice message once the laptop has heard it. */
    @Synchronized
    fun setText(context: Context, session: String, id: String, text: String) = rewrite(context, session) { line ->
        if (line.optString("id") == id) line.put("text", text) else line
    }

    /** The agent spoke in that chat: everything you sent it before is read. */
    @Synchronized
    fun readAll(context: Context, session: String) {
        val chat = file(context, session)
        // Nothing waiting to be read: no rewrite (this runs on every agent line)
        if (session.isEmpty() || !chat.exists() || !chat.readText().contains("\"status\":\"delivered\"")) return
        rewrite(context, session) { line ->
            if (line.optBoolean("mine") && line.optString("status") == "delivered") line.put("status", "read") else line
        }
    }

    /** Agents' texts in that chat you hadn't seen: now seen. Their ids, for the read receipt. */
    @Synchronized
    fun markSeen(context: Context, session: String): List<String> {
        val chat = file(context, session)
        // Only when one is unseen: opening a chat must not rewrite its file each time
        if (session.isEmpty() || !chat.exists() || chat.readLines().none { "\"tid\"" in it && "\"seen\":true" !in it }) return emptyList()
        val ids = mutableListOf<String>()
        rewrite(context, session) { line ->
            val tid = line.optString("tid")
            if (tid.isNotEmpty() && !line.optBoolean("seen")) { ids.add(tid); line.put("seen", true) } else line
        }
        return ids
    }

    private fun rank(status: String) = listOf("", "waiting", "sent", "delivered", "read").indexOf(status)

    private fun rewrite(context: Context, session: String, change: (JSONObject) -> JSONObject) {
        val chat = file(context, session)
        if (session.isEmpty() || !chat.exists()) return
        val lines = chat.readLines().map { raw -> try { change(JSONObject(raw)).toString() } catch (_: Exception) { raw } }
        val aside = File(chat.path + ".new")
        aside.writeText(lines.joinToString("\n", postfix = "\n"))
        aside.renameTo(chat)
        lasts[session] = lines.lastOrNull { it.isNotBlank() }?.let { try { JSONObject(it) } catch (_: Exception) { null } }
    }

    @Synchronized
    fun load(context: Context, session: String): List<JSONObject> {
        val chat = file(context, session)
        if (session.isEmpty() || !chat.exists()) return emptyList()
        return chat.readLines().takeLast(KEEP).mapNotNull { try { JSONObject(it) } catch (_: Exception) { null } }
    }
}
