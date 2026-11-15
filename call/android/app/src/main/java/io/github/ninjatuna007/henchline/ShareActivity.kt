package io.github.ninjatuna007.henchline

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.EditText
import android.widget.LinearLayout
import org.json.JSONObject

/**
 * Henchline in the phone's Share menu: any file (pictures, videos, audio, zips, documents…) or text from any
 * app, for the agent you are talking to. You can add a few words; then UploadService takes it to the laptop
 * and that agent is told where it landed.
 */
class ShareActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        @Suppress("DEPRECATION")  // the typed forms need Android 13; this app runs from 10
        val uris = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))
            Intent.ACTION_SEND_MULTIPLE -> intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: emptyList()
            else -> emptyList()
        }
        val text = if (uris.isEmpty()) intent.getStringExtra(Intent.EXTRA_TEXT) ?: "" else ""
        if (uris.isEmpty() && text.isBlank()) return finish()
        if (Files.origin(this) == null) {
            AlertDialog.Builder(this).setTitle("Henchline").setMessage("Set the laptop's address in Henchline first.")
                .setPositiveButton("OK", null).setOnDismissListener { finish() }.show()
            return
        }
        val names = uris.map { Files.nameOf(this, it) }
        val (session, agent) = current()
        val note = EditText(this).apply { hint = "Add a few words (optional)" }
        AlertDialog.Builder(this)
            .setIcon(R.mipmap.ic_launcher)
            .setTitle("Share with $agent")
            .setMessage(if (uris.isEmpty()) text.take(300) else names.joinToString("\n").take(600))
            .setView(LinearLayout(this).apply { setPadding(48, 8, 48, 0); addView(note) })
            .setPositiveButton("Send") { _, _ ->
                startForegroundService(UploadService.intent(this, uris, names, text, note.text.toString(), session))
            }
            .setNegativeButton("Cancel", null)
            .setOnDismissListener { finish() }
            .show()
    }

    /** On a call, the agent whose chat is showing; otherwise the laptop picks whoever you talked to last. */
    private fun current(): Pair<String, String> {
        val agents = CallService.Bus.lastAgents?.let { JSONObject(it) } ?: return "" to "your crew"
        val session = agents.optString("current")
        val list = agents.optJSONArray("agents") ?: return "" to "your crew"
        for (i in 0 until list.length()) {
            val one = list.getJSONObject(i)
            if (one.optString("session") == session) return session to one.optString("name").substringBefore(",")
        }
        return "" to "your crew"
    }
}
