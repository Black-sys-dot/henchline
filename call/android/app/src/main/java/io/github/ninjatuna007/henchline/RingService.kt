package io.github.ninjatuna007.henchline

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.UnifiedPush
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

/**
 * An agent rang (`handsfree ring` on the laptop), woken through UnifiedPush: the phone's ntfy app is the
 * distributor, so no Google services. The laptop learns this app's push address at the start of every call
 * (CallService sends it), keeps it, and posts {"agent", "why"} to it; here that becomes an incoming call.
 */
class RingService : PushService() {

    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        getSharedPreferences("call", MODE_PRIVATE).edit().putString(ENDPOINT, endpoint.url).apply()
        CallService.Bus.service?.send(JSONObject().put("ring_endpoint", endpoint.url).toString())
    }

    override fun onMessage(message: PushMessage, instance: String) {
        val ring = try {
            JSONObject(String(message.content))
        } catch (_: Exception) {
            JSONObject()
        }
        if (CallService.Bus.service != null) return  // on a call already: the agent is heard there
        ringNow(this, ring.optString("agent", "handsfree"), ring.optString("why"))
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        getSharedPreferences("call", MODE_PRIVATE).edit().remove(ENDPOINT).apply()
    }

    override fun onUnregistered(instance: String) {
        getSharedPreferences("call", MODE_PRIVATE).edit().remove(ENDPOINT).apply()
    }

    companion object {
        const val ENDPOINT = "ring_endpoint"
        const val CHANNEL = "ring"
        const val NOTIFICATION = 2
        const val RING_SECONDS = 30L  // then it is a missed call

        /** Ask the phone's ntfy app (or another distributor) for a push address; a no-op once registered. */
        fun register(context: Context) {
            UnifiedPush.tryUseCurrentOrDefaultDistributor(context) { ok ->
                if (ok) UnifiedPush.register(context, "default", "Henchline: your agents ring you", null)
            }
        }

        /** The incoming call: a full-screen screen on a locked phone, a heads-up notification otherwise. */
        fun ringNow(context: Context, agent: String, why: String) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "Incoming calls", NotificationManager.IMPORTANCE_HIGH)
                .apply { setSound(null, null) })  // IncomingActivity rings, with the agent's own tone
            val screen = Intent(context, IncomingActivity::class.java)
                .putExtra(IncomingActivity.AGENT, agent).putExtra(IncomingActivity.WHY, why)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
            val open = PendingIntent.getActivity(context, 3, screen, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val name = IncomingActivity.nameOf(agent)
            manager.notify(NOTIFICATION, Notification.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("$name is calling")
                .setContentText(why.ifEmpty { "Your crew wants to talk." })
                .setCategory(Notification.CATEGORY_CALL)
                .setFullScreenIntent(open, true)
                .setContentIntent(open)
                .setOngoing(true)
                .setTimeoutAfter(RING_SECONDS * 1000)
                .build())
        }
    }
}
