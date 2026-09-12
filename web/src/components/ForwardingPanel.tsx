import { useCallback, useEffect, useState } from "react";

import {
  ForwardingChannel,
  ForwardingLog,
  ForwardingRule,
  ForwardingRuleSpec,
  createForwardingRule,
  deleteForwardingRule,
  listForwardingLogs,
  listForwardingRules,
  testForwardingRule,
  updateForwardingRule,
} from "../adminApi";

const EMPTY_SPEC: ForwardingRuleSpec = {
  name: "",
  enabled: true,
  channel: "wecom",
  target: "",
  match_device_id: "",
  match_phone: "",
  match_sender: "",
  match_keyword: "",
  template: "",
};

const CHANNEL_PLACEHOLDERS: Record<string, string> = {
  wecom: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
  dingtalk: "https://oapi.dingtalk.com/robot/send?access_token=...",
  feishu: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
  bark: "https://api.day.app/你的Key",
  webhook: "https://你的接收地址（POST JSON）",
};

export function ForwardingPanel({ token }: { token: string }) {
  const [channels, setChannels] = useState<ForwardingChannel[]>([]);
  const [rules, setRules] = useState<ForwardingRule[]>([]);
  const [logs, setLogs] = useState<ForwardingLog[]>([]);
  const [notice, setNotice] = useState("加载中…");
  const [busy, setBusy] = useState(false);
  const [spec, setSpec] = useState<ForwardingRuleSpec>(EMPTY_SPEC);
  const [editingId, setEditingId] = useState<string | null>(null);

  const refresh = useCallback(
    async (finalMessage?: string) => {
      try {
        const [rulesPayload, logsPayload] = await Promise.all([
          listForwardingRules(token),
          listForwardingLogs(token, 30),
        ]);
        setChannels(rulesPayload.channels);
        setRules(rulesPayload.rules);
        setLogs(logsPayload.logs);
        setNotice(finalMessage ?? `已加载 ${rulesPayload.rules.length} 条规则、${logsPayload.logs.length} 条日志`);
      } catch (error) {
        setNotice(`加载失败：${String(error)}`);
      }
    },
    [token],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSave = useCallback(async () => {
    if (!spec.name.trim()) {
      setNotice("请填写规则名称");
      return;
    }
    setBusy(true);
    try {
      if (editingId) {
        await updateForwardingRule(token, editingId, spec);
        await refresh(`规则「${spec.name}」已更新`);
      } else {
        await createForwardingRule(token, spec);
        await refresh(`规则「${spec.name}」已创建`);
      }
      setSpec(EMPTY_SPEC);
      setEditingId(null);
    } catch (error) {
      setNotice(`保存失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }, [refresh, spec, editingId, token]);

  const handleToggle = useCallback(
    async (rule: ForwardingRule) => {
      setBusy(true);
      try {
        await updateForwardingRule(token, rule.id, { name: rule.name, enabled: !rule.enabled });
        await refresh(`规则「${rule.name}」已${rule.enabled ? "停用" : "启用"}`);
      } catch (error) {
        setNotice(`操作失败：${String(error)}`);
      } finally {
        setBusy(false);
      }
    },
    [refresh, token],
  );

  const handleDelete = useCallback(
    async (rule: ForwardingRule) => {
      if (!window.confirm(`确定删除规则「${rule.name}」？`)) return;
      setBusy(true);
      try {
        await deleteForwardingRule(token, rule.id);
        if (editingId === rule.id) {
          setEditingId(null);
          setSpec(EMPTY_SPEC);
        }
        await refresh(`规则「${rule.name}」已删除`);
      } catch (error) {
        setNotice(`删除失败：${String(error)}`);
      } finally {
        setBusy(false);
      }
    },
    [editingId, refresh, token],
  );

  const handleTest = useCallback(
    async (rule: ForwardingRule) => {
      setNotice(`正在向 ${rule.channel} 发送测试消息…`);
      try {
        const result = await testForwardingRule(token, rule.id);
        await refresh(`${result.ok ? "✅" : "❌"} ${result.message}`);
      } catch (error) {
        setNotice(`测试失败：${String(error)}`);
      }
    },
    [refresh, token],
  );

  const startEdit = useCallback((rule: ForwardingRule) => {
    setEditingId(rule.id);
    setSpec({
      name: rule.name,
      enabled: rule.enabled,
      channel: rule.channel,
      target: "",
      match_device_id: rule.match_device_id ?? "",
      match_phone: "",
      match_sender: rule.match_sender,
      match_keyword: rule.match_keyword,
      template: rule.template,
    });
  }, []);

  return (
    <section aria-labelledby="forwarding-title">
      <div className="section-heading">
        <div>
          <h2 id="forwarding-title">短信转发规则</h2>
          <span className="section-count">新短信入库后按规则匹配，推送到外部渠道；全部条件留空表示转发所有短信</span>
        </div>
        <button className="secondary-button" onClick={() => void refresh()} disabled={busy}>
          刷新
        </button>
      </div>
      <div role="status" className="credential-help" aria-live="polite">
        {notice}
      </div>

      <div className="runtime-warning-row">
        <span>模板变量：{"{{sender}} {{body}} {{phone}} {{code}} {{received_at}}"}</span>
        <span>匹配条件全部留空 = 转发所有短信；关键词支持英文逗号分隔多个（任一命中）</span>
      </div>

      <table aria-label="转发规则列表">
        <thead>
          <tr>
            <th>名称</th>
            <th>渠道 / 目标</th>
            <th>匹配条件</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rules.length === 0 ? (
            <tr>
              <td colSpan={5} className="empty-state">
                还没有转发规则，用下方表单创建第一条。
              </td>
            </tr>
          ) : (
            rules.map((rule) => (
              <tr key={rule.id}>
                <td>{rule.name}</td>
                <td>
                  [{rule.channel}] {rule.target_masked}
                </td>
                <td>
                  {rule.match_device_id ? `设备 ${rule.match_device_id.slice(0, 8)}… · ` : ""}
                  {rule.match_phone_masked ? `号码 ${rule.match_phone_masked} · ` : ""}
                  {rule.match_sender ? `发送方含 ${rule.match_sender} · ` : ""}
                  {rule.match_keyword ? `关键词 ${rule.match_keyword}` : "无关键词"}
                  {!rule.match_device_id && !rule.match_phone_masked && !rule.match_sender && !rule.match_keyword
                    ? "全部短信"
                    : ""}
                </td>
                <td>{rule.enabled ? "启用" : "停用"}</td>
                <td>
                  <button onClick={() => void handleTest(rule)} disabled={busy}>
                    测试
                  </button>
                  <button onClick={() => startEdit(rule)} disabled={busy}>
                    编辑
                  </button>
                  <button onClick={() => void handleToggle(rule)} disabled={busy}>
                    {rule.enabled ? "停用" : "启用"}
                  </button>
                  <button onClick={() => void handleDelete(rule)} disabled={busy}>
                    删除
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      <h3>{editingId ? "编辑规则" : "新建规则"}</h3>
      <form
        className="forwarding-form"
        aria-label="转发规则表单"
        onSubmit={(event) => {
          event.preventDefault();
          void handleSave();
        }}
      >
        <label>
          规则名称
          <input value={spec.name} onChange={(event) => setSpec({ ...spec, name: event.target.value })} />
        </label>
        <label>
          推送渠道
          <select
            value={spec.channel}
            onChange={(event) => setSpec({ ...spec, channel: event.target.value })}
          >
            {(channels.length ? channels : Object.entries(CHANNEL_PLACEHOLDERS).map(([id]) => ({ id, label: id }))).map(
              (channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.label}
                </option>
              ),
            )}
          </select>
        </label>
        <label>
          推送地址（{editingId ? "留空保持已保存地址" : "必填"}）
          <input
            value={spec.target}
            placeholder={CHANNEL_PLACEHOLDERS[spec.channel] ?? "推送地址"}
            onChange={(event) => setSpec({ ...spec, target: event.target.value })}
          />
        </label>
        <label>
          匹配设备 ID（留空 = 任意设备）
          <input
            value={spec.match_device_id}
            onChange={(event) => setSpec({ ...spec, match_device_id: event.target.value })}
          />
        </label>
        <label>
          匹配手机号（留空 = 任意号码）
          <input
            value={spec.match_phone}
            onChange={(event) => setSpec({ ...spec, match_phone: event.target.value })}
          />
        </label>
        <label>
          匹配发送方（包含即命中，留空 = 任意）
          <input
            value={spec.match_sender}
            onChange={(event) => setSpec({ ...spec, match_sender: event.target.value })}
          />
        </label>
        <label>
          匹配关键词（英文逗号分隔，任一命中；留空 = 任意内容）
          <input
            value={spec.match_keyword}
            onChange={(event) => setSpec({ ...spec, match_keyword: event.target.value })}
          />
        </label>
        <label>
          内容模板（可选；支持 {"{{sender}} {{body}} {{phone}} {{code}}"}）
          <textarea
            value={spec.template}
            rows={3}
            onChange={(event) => setSpec({ ...spec, template: event.target.value })}
          />
        </label>
        <label className="checkline">
          <input
            type="checkbox"
            checked={spec.enabled}
            onChange={(event) => setSpec({ ...spec, enabled: event.target.checked })}
          />
          启用该规则
        </label>
        <button type="submit" disabled={busy}>
          {editingId ? "保存修改" : "创建规则"}
        </button>
        {editingId && (
          <button
            type="button"
            onClick={() => {
              setEditingId(null);
              setSpec(EMPTY_SPEC);
            }}
            disabled={busy}
          >
            取消编辑
          </button>
        )}
      </form>

      <h3>转发日志（保留 7 天）</h3>
      <table aria-label="转发日志">
        <thead>
          <tr>
            <th>时间</th>
            <th>规则</th>
            <th>渠道 / 目标</th>
            <th>状态</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {logs.length === 0 ? (
            <tr>
              <td colSpan={5} className="empty-state">
                暂无转发记录
              </td>
            </tr>
          ) : (
            logs.map((log) => (
              <tr key={log.id}>
                <td>{log.created_at}</td>
                <td>{log.rule_name}</td>
                <td>
                  [{log.channel}] {log.target_masked}
                </td>
                <td>{log.status}</td>
                <td>{log.error ?? "-"}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>
  );
}
