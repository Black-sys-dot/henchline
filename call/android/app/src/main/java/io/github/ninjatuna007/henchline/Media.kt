package io.github.ninjatuna007.henchline

import android.content.Context
import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Which saved files came from which chat (the user's wish, 2026-11-16: clearing a chat or deleting a message can
 * take its files off the phone too). A file an agent sent is saved to Downloads/Henchline when it is opened; this
 * keeps, per saved copy, the chats it was opened from. Copies saved before this was kept are found through the
 * chats' own lines (their links) and the links remembered in Files.
 */
object Media {
    private fun store(context: Context) = File(context.filesDir, "chats/media.json")

    private fun read(context: Context): JSONObject =
        try { JSONObject(store(context).readText()) } catch (_: Exception) { JSONObject() }

    private fun write(context: Context, all: JSONObject) {
        val file = store(context)
        file.parentFile?.mkdirs()
        val aside = File(file.path + ".new")
        aside.writeText(all.toString())
        aside.renameTo(file)
    }

    /** A saved copy opened from that chat. */
    @Synchronized
    fun record(context: Context, saved: Uri, address: String, name: String, session: String) {
        if (session.isEmpty()) return
        val all = read(context)
        val entry = all.optJSONObject(saved.toString()) ?: JSONObject().put("address", address).put("name", name)
            .put("sessions", JSONArray())
        val sessions = entry.getJSONArray("sessions")
        if ((0 until sessions.length()).none { sessions.getString(it) == session }) sessions.put(session)
        all.put(saved.toString(), entry)
        write(context, all)
    }

    /** A saved copy on the phone: where, its name, and its link on the laptop. */
    data class Saved(val uri: Uri, val name: String, val address: String)

    /** Saved copies of these file lines' links (a message, an album, or a whole chat). */
    @Synchronized
    fun savedFor(context: Context, lines: List<JSONObject>): List<Saved> {
        val addresses = lines.mapNotNull { it.optJSONObject("file")?.optString("url") }
            .mapNotNull { Files.resolve(context, it) }.toSet()
        if (addresses.isEmpty()) return emptyList()
        val found = LinkedHashMap<String, Saved>()
        val all = read(context)
        for (key in all.keys()) {
            val entry = all.getJSONObject(key)
            if (entry.optString("address") in addresses) found[key] = Saved(Uri.parse(key), entry.optString("name"), entry.optString("address"))
        }
        val links = context.getSharedPreferences("downloads", Context.MODE_PRIVATE)
        for (address in addresses) links.getString(address, null)?.let { kept ->
            if (kept !in found) found[kept] = Saved(Uri.parse(kept), Uri.parse(address).lastPathSegment ?: "file", address)
        }
        return found.values.filter { exists(context, it.uri) }
    }

    /** Of those, the ones no other chat on the phone links to: deleting them can't take a file from another chat. */
    fun onlyIn(context: Context, session: String, saved: List<Saved>, except: Set<Long> = emptySet()): List<Saved> {
        if (saved.isEmpty()) return saved
        val elsewhere = HashSet<String>()
        for (other in Chats.sessions(context)) for (line in Chats.load(context, other)) {
            if (other == session && line.optLong("at") !in except) {
                line.optJSONObject("file")?.optString("url")?.let { Files.resolve(context, it) }?.let { elsewhere.add(it) }
            } else if (other != session) {
                line.optJSONObject("file")?.optString("url")?.let { Files.resolve(context, it) }?.let { elsewhere.add(it) }
            }
        }
        return saved.filter { it.address !in elsewhere }
    }

    private fun exists(context: Context, uri: Uri) =
        try { context.contentResolver.openFileDescriptor(uri, "r")?.use { true } ?: false } catch (_: Exception) { false }

    /** Saved copies deleted from the phone, with their previews and what was remembered of them. How many went;
     *  a copy the app no longer owns (saved by an earlier install) Android may refuse: left, and not counted. */
    @Synchronized
    fun delete(context: Context, saved: List<Saved>): Int {
        val all = read(context)
        val links = context.getSharedPreferences("downloads", Context.MODE_PRIVATE).edit()
        var gone = 0
        for (copy in saved) {
            val deleted = try { context.contentResolver.delete(copy.uri, null, null) > 0 } catch (_: Exception) { false }
            if (!deleted) continue  // refused: still there, still remembered (no second download of it)
            gone++
            all.remove(copy.uri.toString())
            links.remove(copy.address)
        }
        links.apply()
        write(context, all)
        return gone
    }

    /** The app's cached copies (previews, a PDF's pages) of these deleted lines' links: they go with the lines,
     *  unless a line still on the phone links there too. */
    fun dropPreviews(context: Context, lines: List<JSONObject>) {
        val gone = lines.mapNotNull { it.optJSONObject("file")?.optString("url") }.mapNotNull { Files.resolve(context, it) }.toSet()
        if (gone.isEmpty()) return
        val still = Chats.sessions(context).flatMap { Chats.load(context, it) }
            .mapNotNull { it.optJSONObject("file")?.optString("url") }.mapNotNull { Files.resolve(context, it) }.toSet()
        for (address in gone - still) Files.cacheFile(context, address).delete()
    }

    /** The chat cleared: it no longer counts for any saved copy (the copies themselves stay unless deleted). */
    @Synchronized
    fun forget(context: Context, session: String) {
        val all = read(context)
        for (key in all.keys().asSequence().toList()) {
            val sessions = all.getJSONObject(key).getJSONArray("sessions")
            for (i in sessions.length() - 1 downTo 0) if (sessions.getString(i) == session) sessions.remove(i)
        }
        write(context, all)
    }
}
