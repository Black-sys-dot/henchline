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

    @Synchronized
    fun add(context: Context, session: String, line: JSONObject) {
        if (session.isEmpty()) return
        val chat = file(context, session)
        chat.parentFile?.mkdirs()
        chat.appendText(line.put("at", System.currentTimeMillis()).toString() + "\n")
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
            sweep(context)
        }
    }

    /** Pictures' small copies that no kept line names any more (trimmed away, or its chat pruned) go too. */
    private fun sweep(context: Context) {
        val folder = File(context.filesDir, "chats")
        val named = folder.listFiles { f -> f.isFile && f.name.endsWith(".jsonl") }.orEmpty()
            .flatMap { it.readLines() }.mapNotNull { try { JSONObject(it).optString("thumb").ifEmpty { null } } catch (_: Exception) { null } }
            .toSet()
        File(folder, "thumbs").listFiles()?.filter { it.path !in named }?.forEach { it.delete() }
    }

    @Synchronized
    fun load(context: Context, session: String): List<JSONObject> {
        val chat = file(context, session)
        if (session.isEmpty() || !chat.exists()) return emptyList()
        return chat.readLines().takeLast(KEEP).mapNotNull { try { JSONObject(it) } catch (_: Exception) { null } }
    }
}
