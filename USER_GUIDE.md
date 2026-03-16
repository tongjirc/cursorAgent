# Cherry-Pick Bot — User Guide / 用户指南

## Getting Started / 快速开始

The bot manages the cherry-pick queue for the daily cocktail build branch. Instead of coordinating manually in Slack ("pushing" / "queuing"), you talk to the bot and it handles the order.

Bot 管理每日 cocktail build branch 的 cherry-pick 队列。不再需要在 Slack 中手动协调 ("pushing" / "queuing")，跟 bot 说一声就行。

### Step 1: Set the daily branch (once per day, by the build coordinator)

```
@Bot set-branch sandbox/dning/l2pp_2026-03-13_ppc_cocktail_candidate_2
```

After this, all commands can omit the branch name.
设置后，所有命令都可以省略 branch 名称。

---

## Two Ways to Cherry-Pick / 两种 Cherry-Pick 方式

### Option A: Let Bot do it automatically / 让 Bot 自动处理

Best for: CLs with no expected conflicts.
适合：预期没有冲突的 CL。

```
@Bot cherry-pick 769290
```

Bot will cherry-pick → run tests → push → report. You just wait.
Bot 会自动 cherry-pick → 跑测试 → push → 汇报。你只需等待。

### Option B: Do it yourself (manual queue) / 自己手动做

Best for: CLs with known conflicts, complex merges, or multiple dependent CLs.
适合：已知有冲突的 CL、复杂合并、或多个互相依赖的 CL。

```
@Bot queue
```

Bot will put you in line and notify you when it's your turn.
Bot 会把你排上队，轮到你时通知你。

---

## The Core Flow / 核心流程

Every interaction with the bot follows the same simple pattern:
跟 bot 的每次交互都遵循同一个简单模式：

```
1. You send a command        →  Bot queues it
   你发送命令                    Bot 排队

2. When it's your turn       →  Bot @mentions you
   轮到你时                     Bot @你

3. You (or anyone) reply     →  "takeover" to claim
   你（或任何人）回复             "takeover" 来接管

4. You work + push           →  Timer auto-extends
   你工作 + push                 计时器自动续期

5. You reply "done"          →  Bot reports results
   你回复 "done"                 Bot 汇报结果
```

If nobody replies `takeover` within 5 minutes, bot skips to the next task.
如果 5 分钟内没人回复 `takeover`，bot 自动跳到下一个任务。

---

## Command Quick Reference / 命令速查

### Cherry-Pick / Revert

| Command | What it does |
|---------|-------------|
| `cherry-pick <ref>` | Auto cherry-pick + test + push / 自动 CP + 测试 + push |
| `step-cp <ref1,ref2,...>` | CP each CL individually, skip failures / 逐个 CP，失败跳过（推荐） |
| `batch-cp <ref1,ref2,...>` | CP all at once, rollback all on failure / 一起 CP，失败全部回滚 |
| `revert <ref>` | Revert a commit / 回滚一个 commit |
| `batch-revert <ref1,ref2,...>` | Revert multiple / 回滚多个 |
| `run-test` | Run tests only / 仅跑测试 |

### Manual Queue

| Command | What it does |
|---------|-------------|
| `queue` | Reserve your spot / 排队占位 |
| `takeover` | Claim the branch (anyone can) / 接管 branch（任何人可以） |
| `done` | I'm finished / 我搞完了 |
| `skip` | Not ready, go to back of line / 没准备好，排到队尾 |

### Queue Management

| Command | What it does |
|---------|-------------|
| `urgent <cmd>` | Cut the line / 紧急插队 |
| `cancel 0` | Cancel current task / 取消当前任务 |
| `cancel <N>` | Cancel queued task #N / 取消排队中第 N 个任务 |
| `status` | Show queue / 查看队列 |
| `help` | Show all commands / 显示所有命令 |

### Ref formats / 引用格式

All commands accept / 所有命令都支持:
- Commit hash: `4567f31efd2a` or full 40-char
- Gerrit change number: `769290`
- Gerrit Change-Id: `I259af1231c590e2c...`

---

## Best Practices / 最佳实践

### When to use `cherry-pick` vs `queue` / 什么时候用自动 vs 手动

| Situation | Use | Why |
|-----------|-----|-----|
| Clean CL, no expected conflicts | `cherry-pick` | Bot handles everything automatically |
| Known conflicts or complex merge | `queue` | You need manual control to resolve |
| Multiple dependent CLs | `queue` | CP them together in the right order |
| Quick parameter change / config CL | `cherry-pick` | Fast, low risk |
| Large CL touching many files | `queue` | Higher conflict risk |

### When to use `step-cp` vs `batch-cp` / step-cp 和 batch-cp 的区别

| | `step-cp` | `batch-cp` |
|---|-----------|-----------|
| Test strategy | Test each CL individually | Test once after all CLs |
| On failure | Skip the failing CL, push the rest | Rollback everything |
| Speed | Slower (N tests) | Faster (1 test) |
| Safety | Safer — good CLs still land | All-or-nothing |
| **Recommendation** | **Use this by default** | Use when CLs are tightly coupled |

### Tips / 小贴士

1. **Always check `status` first** if you're unsure what's happening.
   不确定状态时，先用 `status` 看一下。

2. **Use `step-cp` for multiple CLs** — if one has a conflict, the others still get in.
   多个 CL 时用 `step-cp` — 一个冲突不影响其他。

3. **Anyone can `takeover`** — if your teammate is busy, you can claim their slot and help.
   任何人都能 `takeover` — 队友忙的时候你可以帮他接管。

4. **You don't need to `extend`** — the bot detects your pushes and auto-extends the timer.
   不需要手动 extend — bot 检测到你 push 就会自动续时。

5. **After success, trigger the Alfred build manually** — the bot always reminds you.
   成功后，记得手动触发 Alfred build — bot 每次都会提醒。

6. **`urgent` is for real emergencies** — don't abuse it or the queue loses its value.
   `urgent` 留给真正紧急的情况 — 别滥用，否则排队就没意义了。

---

## What Happens When Things Go Wrong / 出问题时会发生什么

### Bot encounters a conflict / Bot 遇到冲突

Bot rolls back automatically and gives you options:
Bot 自动 rollback 并给你选择：

> ⚠️ Cherry-Pick conflict (rolled back)
> 💡 Reply `takeover` to resolve manually, `cancel 0` to skip, or fix & re-submit

Bot waits 5 minutes for someone to `takeover`. No response → skips to next task.
Bot 等 5 分钟让人 `takeover`。没人回应 → 跳到下一个任务。

### Test fails / 测试失败

Same flow — bot rolls back and waits for `takeover`.
一样的流程 — bot rollback 然后等 `takeover`。

### Infrastructure / auth error / 基础设施/认证错误

Bot retries automatically (up to 2 times). No action needed.
Bot 自动重试（最多 2 次）。你不需要做任何事。

### Bot crashes / Bot 崩溃

Bot auto-restarts within 30 seconds. Admins get a DM notification. The queue is cleared on restart — just re-send your commands.
Bot 在 30 秒内自动重启。Admin 会收到 DM 通知。重启后队列清空 — 重新发送你的命令就行。

---

## FAQ

**Q: Can I use the bot and manual CP at the same time?**
**Q: 可以同时用 bot 和手动 CP 吗？**

Yes! The queue handles both. `cherry-pick` = bot does it. `queue` = you do it. They share the same queue.
可以！队列统一管理。`cherry-pick` = bot 做。`queue` = 你做。它们在同一个队列里。

**Q: What if I `takeover` but realize I can't handle it?**
**Q: 如果我 takeover 了但发现处理不了怎么办？**

Reply `skip` — your task goes to the back of the queue. Or `cancel 0` to cancel entirely.
回复 `skip` — 你的任务排到队尾。或者 `cancel 0` 直接取消。

**Q: Can someone else reply `done` for me?**
**Q: 别人能帮我回复 done 吗？**

No, only the person who `takeover`'d can reply `done` or `skip`. This prevents accidental completion.
不能，只有 `takeover` 的那个人才能回复 `done` 或 `skip`。防止误操作。

**Q: What does the bot do to the git repo?**
**Q: Bot 对 git repo 做了什么？**

For automated tasks: `git checkout branch` → `git cherry-pick` → `run tests` → `git push`. On any failure, it resets to the original HEAD. For manual tasks: bot doesn't touch git at all — you do everything yourself.
自动任务：`git checkout branch` → `git cherry-pick` → 跑测试 → `git push`。任何失败都会 reset 回原始 HEAD。手动任务：bot 完全不碰 git — 全部你自己操作。

**Q: What refs can I use?**
**Q: 可以用什么格式的 ref？**

Commit hash (`4567f31`), Gerrit change number (`769290`), or Gerrit Change-Id (`I259af...`). The bot resolves them automatically.
Commit hash (`4567f31`)、Gerrit change number (`769290`)、或 Gerrit Change-Id (`I259af...`)。Bot 自动解析。
