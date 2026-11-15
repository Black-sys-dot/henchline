package io.github.ninjatuna007.henchline

import android.app.NotificationManager
import android.content.Context
import org.json.JSONObject

/**
 * The ring now under way, if any, for as long as it rings: so the app shows it when opened, a call made by hand
 * while it rings counts as answering it, and answering by any way stops every part of it (2026-11-15: a call
 * made from the app left the ring sounding).
 */
object Ringing {
    private const val KEY = "ringing"

    fun started(context: Context, agent: String, why: String, reply: String?) {
        prefs(context).edit().putString(KEY, JSONObject().put("agent", agent).put("why", why).put("reply", reply ?: "")
            .put("until", System.currentTimeMillis() + RingService.RING_SECONDS * 1000).toString()).apply()
    }

    /** The ring still sounding: {agent, why, reply, until}, or null. */
    fun now(context: Context): JSONObject? {
        val ring = prefs(context).getString(KEY, null)?.let { try { JSONObject(it) } catch (_: Exception) { null } } ?: return null
        return ring.takeIf { it.optLong("until") > System.currentTimeMillis() }
    }

    fun secondsLeft(context: Context): Long = ((now(context)?.optLong("until") ?: 0) - System.currentTimeMillis()) / 1000

    /** Answered, declined or over: the notification, the ring screen and the record all go. */
    fun end(context: Context) {
        prefs(context).edit().remove(KEY).apply()
        context.getSystemService(NotificationManager::class.java).cancel(RingService.NOTIFICATION)
        IncomingActivity.showing?.get()?.let { it.runOnUiThread { it.quietly() } }
    }

    private fun prefs(context: Context) = context.getSharedPreferences("call", Context.MODE_PRIVATE)
}
