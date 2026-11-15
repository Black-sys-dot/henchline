package io.github.ninjatuna007.callagents

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.LinkedBlockingDeque
import java.util.concurrent.TimeUnit

/**
 * The call: the phone's mic to the call server, the agents' voices back, as the web page does,
 * but in a foreground service, so it keeps going with the screen off or the app in the background.
 *
 * The server's protocol is the page's: binary frames are audio (16 kHz s16 out, 24 kHz s16 in),
 * text frames JSON. The phone's own voice-call audio mode cancels its echo, as the browser did.
 */
class CallService : Service() {

    /** What the screen shows: the service is the call, the activity only watches it. */
    object Bus {
        @Volatile var listener: ((String) -> Unit)? = null  // JSON from the server, or {"closed": reason}
        @Volatile var connected = false
        @Volatile var service: CallService? = null
    }

    private var socket: WebSocket? = null
    private var recorder: Thread? = null
    private var player: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var running = false
    private val heard = LinkedBlockingDeque<ByteArray>()  // the agents' voices, waiting to be played
    private val audio by lazy { getSystemService(AudioManager::class.java) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        val server = intent?.getStringExtra(SERVER) ?: return START_NOT_STICKY
        if (running) return START_NOT_STICKY
        running = true
        Bus.service = this
        goForeground()
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "callagents:call").apply { acquire() }
        audio.mode = AudioManager.MODE_IN_COMMUNICATION  // the phone's echo cancelling, and call routing
        route()
        connect(server)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        socket?.close(1000, "hung up")
        socket = null
        recorder?.interrupt()
        player?.interrupt()
        heard.clear()
        if (Build.VERSION.SDK_INT >= 31) audio.clearCommunicationDevice()
        audio.mode = AudioManager.MODE_NORMAL
        wakeLock?.let { if (it.isHeld) it.release() }
        Bus.connected = false
        Bus.service = null
        Bus.listener?.invoke("""{"closed": "Call ended"}""")
        super.onDestroy()
    }

    fun send(json: String) {
        socket?.send(json)
    }

    private fun connect(server: String) {
        val base = Uri.parse(server)
        val origin = "${base.scheme}://${base.authority}"  // the server lets only its own page's origin in
        val url = origin.replaceFirst("http", "ws") + "/call"
        val client = OkHttpClient.Builder().pingInterval(15, TimeUnit.SECONDS).build()
        val request = Request.Builder().url(url).header("Origin", origin).build()
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Bus.connected = true
                startAudio()
                Bus.listener?.invoke("""{"opened": true}""")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                Bus.listener?.invoke(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                heard.offer(bytes.toByteArray())
                while (heard.size > MAX_BEHIND) heard.poll()  // over a second behind: drop the oldest
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                Bus.listener?.invoke(org.json.JSONObject().put("closed", reason.ifEmpty { "Call ended" }).toString())
                stopSelf()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Bus.listener?.invoke(org.json.JSONObject().put("closed", "Couldn't reach the call: ${t.message}").toString())
                stopSelf()
            }
        })
    }

    private fun startAudio() {
        recorder = Thread {
            val size = maxOf(AudioRecord.getMinBufferSize(MIC_RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT), FRAME * 2 * 4)
            val mic = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, MIC_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
            val buffer = ByteArray(FRAME * 2)
            mic.startRecording()
            try {
                while (running) {
                    val got = mic.read(buffer, 0, buffer.size)
                    if (got > 0) socket?.send(buffer.toByteString(0, got))
                }
            } finally {
                mic.stop()
                mic.release()
            }
        }.apply { name = "mic"; start() }
        player = Thread {
            val track = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(AudioFormat.Builder().setSampleRate(SPEAKER_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(SPEAKER_RATE / 5 * 2)
                .build()
            track.play()
            try {
                while (running) {
                    val chunk = heard.poll(200, TimeUnit.MILLISECONDS) ?: continue
                    track.write(chunk, 0, chunk.size)
                }
            } catch (_: InterruptedException) {
            } finally {
                track.stop()
                track.release()
            }
        }.apply { name = "speaker"; start() }
    }

    /** Earbuds or a headset when there are some, else the loudspeaker (a call would pick the earpiece). */
    private fun route() {
        if (Build.VERSION.SDK_INT < 31) {
            @Suppress("DEPRECATION")
            audio.isSpeakerphoneOn = true
            return
        }
        val devices = audio.availableCommunicationDevices
        val preferred = listOf(AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)
        preferred.firstNotNullOfOrNull { type -> devices.firstOrNull { it.type == type } }
            ?.let { audio.setCommunicationDevice(it) }
    }

    private fun goForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Call", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        val hangUp = PendingIntent.getService(this, 1, Intent(this, CallService::class.java).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE)
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_call)
            .setContentTitle("On a call with the agents")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Hang up", hangUp).build())
            .build()
        startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
    }

    companion object {
        const val SERVER = "server"
        const val STOP = "stop"
        const val CHANNEL = "call"
        const val MIC_RATE = 16000  // what hands-free listens at
        const val SPEAKER_RATE = 24000  // Kokoro's own rate
        const val FRAME = MIC_RATE / 50  // 20 ms of samples per message, as the page sends
        const val MAX_BEHIND = 50  // chunks queued before the oldest are dropped: about a second
    }
}
