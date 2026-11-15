package io.github.ninjatuna007.henchline

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.EditText
import android.widget.LinearLayout

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

    /** The agent whose chat Henchline shows (kept across restarts): a share goes to that one, never elsewhere. */
    private fun current(): Pair<String, String> {
        val prefs = getSharedPreferences("call", MODE_PRIVATE)
        val session = prefs.getString("current", "") ?: ""
        val name = prefs.getString("current_name", "")?.substringBefore(",")?.ifEmpty { null } ?: "your agent"
        return session to name
    }
}
