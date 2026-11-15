package io.github.ninjatuna007.henchline

import android.app.AlertDialog
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.provider.Settings

/**
 * Rings in Do Not Disturb: shown, silently (the user, 2026-11-15: "if it's do not disturb, it's probably do
 * not disturb for a reason"), so a ring can still be seen and answered, and is otherwise a missed call. Showing
 * at all needs Do Not Disturb access (HyperOS's Do Not Disturb has no list of allowed apps): with it, the
 * ring's channels keep "bypass Do Not Disturb", and RingService posts them without sound or full screen.
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
            .setTitle("See your crew's calls in Do Not Disturb")
            .setMessage("Turn Henchline on in the list that opens. In Do Not Disturb its calls then show, silently; they never ring.")
            .setPositiveButton("Open the list") { _, _ -> context.startActivity(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)) }
            .setNegativeButton("Not now", null)
            .show()
    }
}
