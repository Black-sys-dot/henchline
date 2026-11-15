package io.github.ninjatuna007.henchline

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Decline on the ring's notification (the phone was in use, so it rang as a banner): the ring stops, and
 *  the laptop hears it was declined. */
class RingActions : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != DECLINE) return
        context.getSystemService(NotificationManager::class.java).cancel(RingService.NOTIFICATION)
        Reply.send(intent.getStringExtra(IncomingActivity.REPLY), "declined")
    }

    companion object {
        const val DECLINE = "io.github.ninjatuna007.henchline.DECLINE"
    }
}
