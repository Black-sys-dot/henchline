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
        ring.optJSONObject("file")?.let { file ->  // `handsfree send` while no call was up
            Files.notify(this, ring.optString("agent", "handsfree"), ring.optString("why"), file.optString("url"),
                file.optString("name", "file"), file.optString("type", "application/octet-stream"), file.optLong("size"))
            return
        }
        if (CallService.Bus.service != null) return  // on a call already: the agent is heard there
        val reply = Reply.valid(ring.optString("reply"))
        Reply.send(reply, "rang")  // it reached the phone
        ringNow(this, ring.optString("agent", "handsfree"), ring.optString("why"), reply)
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

        /** The incoming call. On a locked phone, or with the screen off, Android opens the full-screen ring
         *  (IncomingActivity, which plays the agent's tone and phrase). While the phone is in use it only
         *  shows a banner, which rang silently (2026-11-15): then the banner itself rings, with the agent's
         *  tone over and over, and has Answer and Decline, as a phone app's call does. */
        fun ringNow(context: Context, agent: String, why: String, reply: String? = null) {
            val manager = context.getSystemService(NotificationManager::class.java)
            val flavour = agent.takeIf { it in IncomingActivity.FLAVOURS } ?: "handsfree"
            val inUse = context.getSystemService(android.os.PowerManager::class.java).isInteractive &&
                !context.getSystemService(android.app.KeyguardManager::class.java).isKeyguardLocked
            val channel = if (inUse) bannerChannel(context, manager, flavour) else CHANNEL.also {
                manager.createNotificationChannel(NotificationChannel(CHANNEL, "Incoming calls", NotificationManager.IMPORTANCE_HIGH)
                    .apply { setSound(null, null) })  // IncomingActivity rings, with the agent's own tone
            }
            val screen = Intent(context, IncomingActivity::class.java)
                .putExtra(IncomingActivity.AGENT, agent).putExtra(IncomingActivity.WHY, why)
                .putExtra(IncomingActivity.REPLY, reply)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
            val open = PendingIntent.getActivity(context, 3, screen, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val answer = PendingIntent.getActivity(context, 4, Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ANSWER).putExtra(IncomingActivity.REPLY, reply)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val decline = PendingIntent.getBroadcast(context, 5, Intent(context, RingActions::class.java)
                .setAction(RingActions.DECLINE).putExtra(IncomingActivity.REPLY, reply),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val name = IncomingActivity.nameOf(agent)
            val caller = android.app.Person.Builder().setName(name)
                .setIcon(android.graphics.drawable.Icon.createWithResource(context, MainActivity.avatarOf(flavour)))
                .setImportant(true).build()
            val ring = Notification.Builder(context, channel)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("$name is calling")
                .setContentText(why.ifEmpty { "Your crew wants to talk." })
                .setCategory(Notification.CATEGORY_CALL)
                .setStyle(Notification.CallStyle.forIncomingCall(caller, decline, answer))
                .setFullScreenIntent(open, true)
                .setContentIntent(open)
                .setOngoing(true)
                .setTimeoutAfter(RING_SECONDS * 1000)
                .build()
            if (inUse) ring.flags = ring.flags or Notification.FLAG_INSISTENT  // the tone repeats until answered
            manager.notify(NOTIFICATION, ring)
        }

        /** A channel per agent whose sound is its ringtone: a banner's sound is its channel's. */
        private fun bannerChannel(context: Context, manager: NotificationManager, flavour: String): String {
            val id = "ring_banner_$flavour"
            val tone = android.net.Uri.parse("android.resource://${context.packageName}/raw/ring_$flavour")
            manager.createNotificationChannel(NotificationChannel(id, "${IncomingActivity.nameOf(flavour)} calling while you use the phone",
                NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(tone, android.media.AudioAttributes.Builder()
                    .setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            })
            return id
        }
    }
}
