# IEvidenceLedger spec v1.0 — 决策清单(定案版 2026-09-07)

> 状态:Part 0 前置验证 V1 ✅;V2 待 09-12。A 区 = 直接入 spec;B 区 = 已逐条拍板定案(09-07)。
> 冻结流程:09-12 复核(manifest 实数/名单/冒烟测试)→ 回填 spec-v1.0.md 数值项 → 09-13 封版 → 旧 PRODUCT-INTERFACES-DRAFT.md 标 superseded → 09-14 forge init。

## Part 0 · 冻结前置验证

| # | 验证 | 结果 | 动作 |
|---|---|---|---|
| V1 | RH 主网 4663 无部署白名单 | ✅ 已验:chainId 4663,区块 56,702,726,模拟合约创建(from=0xdEaD 无权限)→ 返回 runtime 字节码;gas 0.32 gwei | 无白名单问题;gas 成本可忽略 |
| V2 | 测试网 46630 真交易权限路径 | ⏳ 待验(09-12) | `cast send --create` 裸发一次合约创建,无源码、不进 repo、不留 commit。**注意:这是冒烟测试,不是 B6 的 iteration ledger——两个地址分开记录,spec §8 各占一格,避免 demo 时混淆** |
| V3 | **主网 4663 gas 充值路径** | ✅ 已解(09-07):首选 **OKX 直接提币至 RH 链主网 4663**(若提币网络列表含 "Robinhood Chain";到账快、无需桥 UI/L1 手续费);备选 = Arbitrum canonical bridge(portal.arbitrum.io,对所有地区开放,~10 min)。成本小(~0.01–0.05 ETH,L1 data fee 随 calldata,批量 41B/轮已最小化) | 发前在 OKX 核对:网络是否含 4663、最小提币额;先小额试提 → `cast balance` 确认到账 → 再提足;收款地址 = `.env` 部署地址 |
| V4 | **测试网端点可达性** | ✅ 已解:`.env` RPC_TESTNET = rpc.testnet.chain.robinhood.com(46630)、RPC_MAINNET = rpc.mainnet.chain.robinhood.com(4663);本地领款时已实测通过。VPS agent "域名无法解析" = 其未拿到端点名,非端点问题 | 端点名喂给 VPS 侧;开发迭代 46630 为主,`anvil --fork $RPC_MAINNET` 仅作本地加速,不替代 46630 终验 |

## A 区 · 已定结论(直接入 spec,不再讨论)

| # | 项 | 结论 | 依据 |
|---|---|---|---|
| A1 | token 名单 | **AAPL AMC GME GOOGL NVDA QQQ RDDT SPY TSLA**(9 个) | VPS manifest 实测:block 55536711 起全期恒定 9 token,无一轮 8 或 10;GOOGL 在已发布报告表格(99.99/99.95/99.53/97.97%),正文只点名有故事的 QQQ/GME/AMC;旧 8 版 = v17 遗留缺 AMC,superseded |
| A2 | 尺寸档 | `[100, 1000, 10000, 100000]` USD(4 档) | 与报告表格、36 = 9×4 一致 |
| A3 | 轮结构 | 36 条规范记录/轮;canon 序列化 `rhdepth-v2`;`roundKeccak` = 规范串 keccak256;逐轮哈希权威 = rounds.jsonl + verify.py + 报告可复核性节 | 生产格式即此;spec 引用锚定,不重编码(见 spec-v1.0 §2) |
| A4 | 权限 | owner-only commit | ✅ |
| A5 | 提交模型 | 批量回填(本地短会话,非实时 relay);哈希来源 = VPS manifest `roundKeccak` | ✅ |
| A6 | 部署路径 | 测试网 46630 迭代 → 主网 4663 终版 | ✅ |
| A7 | 数据范围 | 链上账本只收 RH 线;dexfeed/pmfeed 不入账本(日级 MANIFEST 已在 repo) | 逐轮哈希只此线存在(09-07 实查) |
| A8 | **不可变性硬约束** | **已发布文件只增不改;链上状态永不回写进数据文件。** `rounds.jsonl` 在 MANIFEST.sha256 内,`committed` 字段永不翻 true(它是采集侧遗留字段,不是真相源);链 = "哪些轮已提交"的唯一真相(`latestCommittedBlock()` 一次调用答完);要本地索引 → 独立 append-only `commits.jsonl` {block, txHash, committedAt} | B3 定案(09-07);否则"checksums will always match"当场失效 |

## B 区 · 定案记录(2026-09-07 逐条拍板)

### B1 上链粒度 —— ✅ 定案:逐轮 1×bytes32,commitBatch 批量交易
- 接口:
  ```solidity
  function commitBatch(uint64[] calldata blocks, bytes32[] calldata roundHashes) external onlyOwner;
  // blocks 严格递增、两数组等长;链上逐轮一条记录
  ```
- **补充理由(入 spec §3)**:为什么 storage 而不是只发 event——`getRoundHash()` 是只读 state 函数,读不到 event;0.32 gwei 下 storage 代价可忽略,而丢掉读接口就丢掉"未来可被消费"叙事。
- 批大小:64–128 轮/笔(41 B/轮 calldata → 1,000 轮 ≈ 8–16 笔,09-12 以 manifest 实数修正)。旧"~30 笔"口径作废。

### B2 epoch 键 —— ✅ 定案:只用 `block`,排除 roundTs
- 判据:block 是状态锚点、已在哈希原像内、100ms 出块单调;两轮间隔 30min ≈ 18,000 区块,撞键物理不可能;ts 第三方无法复现,不配进验证链。
- 规则:`require(blocks[i] > lastCommittedBlock)`;storage `mapping(uint64 → CommitRecord)`。ts 仅作展示(event log),不进键。

### B3 canon 版本 —— ✅ 定案(一半):canon 上链;**committed 回写取消**
- canon 上链 ✅:`CommitRecord { bytes32 roundHash; uint8 canonVersion; }`;canon 2 = rhdepth-v2;v1-defective 隔离目录永不上链;首个 v2 轮 = block 54,088,399。
- **committed 回写 ❌ 取消**:证据 = MANIFEST.sha256 覆盖 rounds.jsonl;回写任何一个 false→true 即破坏已发布校验和。替代 = A8 硬约束 + 可选 append-only commits.jsonl。
- 影响面:verify.py 加 canon 校验;回填脚本幂等靠 B2 严格递增(重复 block 直接 revert)。

### B4 haircut(eventTs) —— ✅ 定案:砍掉,记录"动机不可追溯"
- 证据:全项目 grep(haircut/eventTs/event_ts)→ 零匹配,代码与文档中不存在,仅存活于早期草稿。
- spec 处置(原文入 §10):"haircut(eventTs) 曾出现在早期草稿,无法追溯其设计动机,代码中无对应实现,故不纳入 v1.0。若日后重新提出,需附明确的失效场景。"
- 原则:不为不明字段编理由;成员资格取决于规则而非偶然。

### B5 只读接口 —— ✅ 定案:`getRoundHash` + 边界声明
```solidity
function getRoundHash(uint64 blockRef) external view returns (bytes32, uint8 canon);
function latestCommittedBlock() external view returns (uint64);
```
- 边界声明(入 README/demo):链是公证处,不是数据仓库;接口答"这一轮被记录过吗、指纹是什么",不答"价格是多少"。

### B6 测试网账本 —— ✅ 定案:保留公开 + 标注 superseded
- 措辞**冻结时就写好**(不 demo 前补):测试网地址标注 "iteration ledger, superseded by mainnet";主网 = 正式账本。
- 与 V2 冒烟测试地址分开记录(spec §8)。
- 判据:与保留 v17 同一套逻辑;"试错不藏" = 可见证据。

## C 区 · demo/叙事口径(冻结时定死,避免台上被问)

- **C1** verify 闭环只对 RH 线逐轮成立;另两线日级 MANIFEST → demo 主动说:"链上锚定的是 Robinhood Chain 这条线;另两条是日级校验和,在路线图上"。
- **C2** 数据早于窗口 = 优点:"测量早于窗口是设计使然——时间序列三周造不出来,这正是价值。窗口里造的是证明层:让不可重建的数据变成任何人可复核的东西。"
- **C3** 主网回填语句(随 B1):"主网账本逐轮覆盖从第 1 轮(09-04)到今天全部 ~1,000 轮,任何一轮可复算比对"——不是"可验到天"。

## B 区补记(2026-09-12,冻结前最后一批决定)

- **B7 · owner 两步转移:纳入。** `transferOwnership` + `acceptOwnership`。理由:没有它,owner key 一旦出问题,唯一出路是重新部署(地址变、引用断)。**边界**:只救"已知泄露、尚未被使用"的窗口;攻击者已动手则救不了。
- **B8 · 提交时区块上界:否决。** `require(blockRef <= block.number)` 在数据源链上有效,但实测 46630 块高 118,102,404 ≫ 4663 轮次区块号 54–60M ⇒ 迭代账本上形同虚设;采用会导致主网/测试网实现分叉,破坏"测的就是要部署的"。替代:key 卫生 + B7 + D9 看门狗。
- **B9 · 存储预算口径纠正。** 依据 `ArbGasInfo.perStorageAllocation`(2.0147e12 wei/槽)+ 09-12 实测部署(59,385 gas、0.01 gwei、L1 部分 4,055):回填成本主体是**每槽 storage allocation**,1,042 轮朴素两槽 ≈ **0.0042 ETH**;早先按 0.32 gwei 推出的 0.0133 ETH **偏保守约 3 倍,已撤回**。真正的数由 46630 小批实测给出。
- **B10 · 密钥操作纪律(运行侧,非合约)。** 私钥只进加密 keystore(`--account deployer`),不进 `.env`/命令行/聊天/剪贴板;`.env` 只放 RPC;VPS 既不拿 key 也不拿 keystore;处理私钥前退出远程工具(退出≠最小化);GitHub 开 2FA(锚点是 git 历史,历史不可改写才有意义)。详见报告仓库外的 `LOCAL-SETUP.md`。

## B 区补记(2026-09-14,部署前)

- **B11 · 单调水印的逃生口:记账本之外的锚,部署前写入 spec。纳入。**
  - **失效场景(变更流程要求)**:owner 热钥误提交一条 `block = 99,999,999`(手打错 / 脚本读错字段 / 键泄露)
    ⇒ 水印被推高到远超数据源链当前高度(6,240 万)⇒ **此后所有合法提交永久 revert,合约无回退手段**。
    这不是假想:日常提交由 VPS 上的热钥自动执行,而 D9 看门狗只能告警,不能撤销。
  - **结论**:逃生口不在合约里(也不该有,那正是 append-only 的意思),而在**锚的多重性** ——
    **账本不是唯一锚,git 历史才是**。最坏后果是**重新部署 + 重新公布地址**,不是证据丢失。
  - **为什么必须部署前写**:事后第一次说出口,听起来就是借口。与 §5 的边界补记(2026-09-12)同一动机。
  - **三层链下守卫**(不动合约、不动冻结接口):
    ```
    ① 提交侧拒绝 block > 数据源链(4663)链头 —— 【不是】账本所在链的头(B8 的同一条理由:46630 头 ≈119M)
       落地:tools/round_count.py --source-rpc,缺 RPC 时打印 SKIPPED 而非静默放过
    ② 部署脚本拒绝非预期 chainId(必须显式 LEDGER_CHAIN_ID;白名单两个值不足以防手滑打到主网)
    ③ owner 热钥纪律 + D9 看门狗
    ```
  - **落地位置**:spec-v1.0.md §4.2 末条(标注 B11 + 日期 + 失效场景)。
  - 判据:**约束放在失败发生的地方,不放在合约里**(与 §10 拒绝区块上界同源)。

- **B12 · 水印只留一个入口;并写明"返回零 ⇒ 未提交"靠什么成立。纳入。**
  - **失效场景 A(变更流程要求)**:§3 原草案写 `uint64 public lastCommittedBlock` ⇒ 自动生成
    `lastCommittedBlock()` getter,而 §5 / B5 命名的是 `latestCommittedBlock()`。
    **同一事实两个入口,都返回 uint64,公开 ABI 里无从分辨哪个权威** ⇒ 消费者(如 D4 的锚点查询)
    只能靠猜。两者今天行为一致,但**一旦哪天分叉(改名、首次提交前的返回值不同),挑错的那个会静默坏掉** ——
    而"公开 ABI 是第三方消费的那份东西",这是 P4 的最坏位置。
    ⇒ 处置:状态变量改 `internal`,ABI 里只留 `latestCommittedBlock()`(与 §5 / B5 一致)。
    **这不是给冻结接口加东西,是去掉一个冻结接口从未命名的多余出口**;方向与 B8/§10 拒绝加约束相反,不冲突。
  - **失效场景 B**:`getRoundHash` 的"缺席"语义**不是合约不变量**。
    ① `canon == 0 ⟺ 未提交` —— **2026-09-14 已加固为合约不变量**:构造函数 `revert InvalidCanon(0)`。
       加固前它只是部署侧保证(部署脚本的映射表里只有 `"rhdepth-v2" → 2`),
       而**以 `canon_ = 0` 部署会成功、提交会成功、CI 会绿、D4 会报告"全部未上链"** ——
       P0 那一问("若这个结果是错的,系统里什么会报错?")的答案是【没有】。
       而接口文档声明了那条推断 ⇒ **保证必须和它被声明的地方在一起**:
       第三方在 Blockscout 上看到的是合约,不是别人机器上的脚本。边界取 `== 0` 不取 `< 2`
       (0 是缺席值,是最小边界;`< 2` 会把 §7 版本表焊进合约)。
    ② 合约**不**拒绝零哈希(与 B8 同向:不必要的约束不进冻结接口),所以"零哈希 ⇒ 未提交"
       **只由提交侧保证不提交零哈希**而成立。
    ⇒ 已核:真实载荷 469 轮零哈希 0 个;`tools/round_count.py` 把零哈希列为前置校验。
    ⇒ spec §3 已写明 D4 读取端**可以依赖 `canon == 0`**(合约不变量);
      依赖零哈希时必须同时引用提交侧保证。

## 冻结流程(更新)

1. **09-12**:VPS manifest 实数核名单/轮数(A1/A2/§6 数值回填);冒烟测试 V2;spec-v1.0.md 数值项回填
2. 09-13:封版(spec-v1.0.md 状态 → FROZEN)
3. 旧 PRODUCT-INTERFACES-DRAFT.md 标 superseded(8 token 版,缺 AMC,含 GBK 修复残留)
4. 09-14:forge init 照 spec 实现

## 附注
- 主网回填范围:首个 v2 轮(block 54,088,399)起;v1-defective 轮次(09-03.pre-pinned-block / 09-04.rhdepth-v1-defective)永不上链,错误档案在案。
