package io.smsrelay.collector

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

object QueueState {
    const val PENDING = "PENDING"
    const val UPLOADED = "UPLOADED"
    const val AUTH_BLOCKED = "AUTH_BLOCKED"
    const val CONFLICT = "CONFLICT"
    const val PERMANENT_FAILURE = "PERMANENT_FAILURE"
}

@Entity(
    tableName = "relay_queue",
    indices = [
        Index(value = ["client_message_id"], unique = true),
        Index(value = ["state", "created_at"]),
    ],
)
data class SmsRelayQueueEntity(
    @PrimaryKey(autoGenerate = true)
    @ColumnInfo(name = "row_id")
    val rowId: Long = 0,
    @ColumnInfo(name = "client_message_id")
    val clientMessageId: String,
    @ColumnInfo(name = "received_at")
    val receivedAt: String,
    @ColumnInfo(name = "sender_ciphertext")
    val senderCiphertext: String?,
    @ColumnInfo(name = "body_ciphertext")
    val bodyCiphertext: String?,
    @ColumnInfo(name = "sim_slot")
    val simSlot: Int?,
    @ColumnInfo(name = "subscription_id")
    val subscriptionId: Int? = null,
    @ColumnInfo(name = "carrier_name")
    val carrierName: String? = null,
    @ColumnInfo(name = "phone_number_ciphertext")
    val phoneNumberCiphertext: String? = null,
    val state: String = QueueState.PENDING,
    val attempts: Int = 0,
    @ColumnInfo(name = "server_message_id")
    val serverMessageId: String? = null,
    @ColumnInfo(name = "last_error")
    val lastError: String? = null,
    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),
)

@Dao
interface SmsRelayQueueDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    fun insert(entity: SmsRelayQueueEntity): Long

    @Query("SELECT * FROM relay_queue WHERE state = 'PENDING' ORDER BY created_at, row_id LIMIT :limit")
    fun pending(limit: Int): List<SmsRelayQueueEntity>

    @Query("SELECT COUNT(*) FROM relay_queue")
    fun countAll(): Int

    @Query("SELECT COUNT(*) FROM relay_queue WHERE state = 'PENDING'")
    fun countPending(): Int

    @Query("DELETE FROM relay_queue WHERE row_id IN (SELECT row_id FROM relay_queue WHERE state IN ('UPLOADED', 'CONFLICT', 'PERMANENT_FAILURE') ORDER BY created_at, row_id LIMIT :count)")
    fun deleteOldest(count: Int): Int

    @Query("UPDATE relay_queue SET state = :state, attempts = attempts + 1, server_message_id = :serverMessageId, last_error = :lastError, sender_ciphertext = NULL, body_ciphertext = NULL, phone_number_ciphertext = NULL WHERE row_id = :rowId")
    fun finish(rowId: Long, state: String, serverMessageId: String?, lastError: String?): Int

    @Query("UPDATE relay_queue SET state = :state, attempts = attempts + 1, server_message_id = :serverMessageId, last_error = :lastError, sender_ciphertext = NULL, body_ciphertext = NULL, phone_number_ciphertext = NULL WHERE client_message_id = :clientMessageId")
    fun finishByClientMessageId(clientMessageId: String, state: String, serverMessageId: String?, lastError: String?): Int

    @Query("UPDATE relay_queue SET state = 'AUTH_BLOCKED', attempts = attempts + 1, last_error = :reason WHERE row_id = :rowId")
    fun markAuthBlocked(rowId: Long, reason: String): Int

    @Query("UPDATE relay_queue SET state = 'AUTH_BLOCKED', attempts = attempts + 1, last_error = :reason WHERE client_message_id = :clientMessageId")
    fun markAuthBlockedByClientMessageId(clientMessageId: String, reason: String): Int

    @Query("UPDATE relay_queue SET attempts = attempts + 1, last_error = :reason WHERE row_id = :rowId")
    fun markRetry(rowId: Long, reason: String): Int

    @Query("UPDATE relay_queue SET attempts = attempts + 1, last_error = :reason WHERE client_message_id = :clientMessageId")
    fun markRetryByClientMessageId(clientMessageId: String, reason: String): Int

    @Query("UPDATE relay_queue SET state = 'PENDING', last_error = NULL WHERE state = 'AUTH_BLOCKED'")
    fun resumeAuthBlocked(): Int
}

@Database(entities = [SmsRelayQueueEntity::class], version = 2, exportSchema = false)
abstract class SmsRelayDatabase : RoomDatabase() {
    abstract fun queueDao(): SmsRelayQueueDao

    companion object {
        @Volatile
        private var instance: SmsRelayDatabase? = null

        fun get(context: Context): SmsRelayDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                SmsRelayDatabase::class.java,
                "sms-relay-queue.db",
            )
                .addMigrations(MIGRATION_1_2)
                .build()
                .also { instance = it }
        }

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(database: SupportSQLiteDatabase) {
                database.execSQL("ALTER TABLE relay_queue ADD COLUMN subscription_id INTEGER")
                database.execSQL("ALTER TABLE relay_queue ADD COLUMN carrier_name TEXT")
                database.execSQL("ALTER TABLE relay_queue ADD COLUMN phone_number_ciphertext TEXT")
            }
        }
    }
}
