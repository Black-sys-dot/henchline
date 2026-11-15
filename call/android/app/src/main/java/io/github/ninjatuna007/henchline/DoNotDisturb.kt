package io.github.ninjatuna007.henchline

import android.app.AlertDialog
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.provider.Settings

/**
 * Rings get through Do Not Disturb (the user's wish, 2026-11-15: HyperOS's Do Not Disturb has no list of
 * allowed apps). With Do Not Disturb access, the ring's channels keep "bypass Do Not Disturb", and the ring
 * plays as an alarm while it is on.
 */
object DoNotDisturb {
    fun on(context: Context) =
        context.getSystemService(NotificationManager::class.java).currentInterruptionFilter > NotificationManager.INTERRUPTION_FILTER_ALL

    fun allowed(context: Context) = context.getSystemService(NotificationManager::class.java).isNotificationPolicyAccessGranted

    /** Once: the system's Do Not Disturb access list, where Henchline is switched on. */
    fun ask(context: Context) {
        val prefs = context.getSharedPreferences("call", Context.MODE_PRIVATE)
        if (allowed(context) || prefs.getBoolean("asked_dnd", false)) return
        prefs.edit().putBoolean("asked_dnd", true).apply()
        AlertDialog.Builder(context)
            .setIcon(R.mipmap.ic_launcher)
            .setTitle("Let your crew ring through Do Not Disturb")
            .setMessage("Turn Henchline on in the list that opens. Only its calls get through; other notifications stay quiet.")
            .setPositiveButton("Open the list") { _, _ -> context.startActivity(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)) }
            .setNegativeButton("Not now", null)
            .show()
    }
}
