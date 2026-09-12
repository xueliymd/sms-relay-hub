package io.smsrelay.manager

import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ManagerSmokeTest {
    @Test
    fun loginScreenContainsPlatformCredentialsAndActions() {
        ActivityScenario.launch(MainActivity::class.java).use {
            listOf("平台地址", "管理员用户名", "管理员密码", "记住密码", "测试连接", "登录平台").forEach { label ->
                onView(withText(label)).check(matches(isDisplayed()))
            }
        }
    }
}
