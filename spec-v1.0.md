# IEvidenceLedger spec v1.0

> 状态:**DRAFT-FINAL**(2026-09-07 拍板完成)——数值项【09-12 回填】;09-13 复核后状态转 **FROZEN**。
> 来源:决策清单 `IEVLEDGER-SPEC-DECISIONS.md`(09-07 定案版)。本文件为唯一规范入口;`PRODUCT-INTERFACES-DRAFT.md`(8 token 版)已 superseded。
> 变更规则:冻结后任何修改须走决策清单 B 区新增流程,并附失效场景(先例:haircut(eventTs),见 §10)。

## 1. 范围

- 上链数据线:**仅 Robinhood Chain 代币化股票可执行深度线**(逐轮哈希存在的那条)。dexfeed / pmfeed 不入本账本(日级 MANIFEST.sha256 在 repo)。
- Token 名单(9):`AAPL AMC GME GOOGL NVDA QQQ RDDT SPY TSLA`
- 尺寸档(4):`[100, 1000, 10000, 100000]` USD
- 轮结构:36 条规范记录/轮(9 × 4)
- 回填范围:自首个 v2 轮 **block 54,088,399**(2026-09-04)起;v1-defective 轮次(隔离目录)永不上链。

## 2. 序列化与哈希(canon = 2,rhdepth-v2)

- 逐轮哈希的**字节级权威 = 已发布的 `rounds.jsonl` + `code/verify.py` + 报告可复核性节**。本 spec 引用锚定,不重编码,避免两处定义漂移。
- 已核实的性质(block 54,088,399 实测):独立重放每轮往返 → 复现 `roundKeccak` 逐字节一致。
- `canon` 版本号映射:`2` = rhdepth-v2。任何口径升级 = 新 canon 版本 + 方法论文档,历史键不受影响(§7)。
- **可入序列的状态(2026-09-11 规格化)**：`status` 字段本来就在原像内；规范上**只有"在钉定区块上可确定复现"的结果才能进入 `rounds.jsonl`**——即 `ok` 与 `no_liquidity`(NotEnoughLiquidity 回滚 = 数据)。
  - 🚫 **瞬态基础设施故障不得落盘**:`rpc_error`、以及"部分池故障但仍拿到报价"的 `ok_partial`(后者最危险——它产出看起来正常、只是偏低的数字,与"流动性枯竭"形状不可区分)。采集器须在**同一 pinned block** 重试;重试耗尽则**丢弃整轮**(写 `failures.jsonl`,不进序列,到齐率如实下降)。
  - 🚫 `revert_other`(非预期回滚)= 缺陷报警,同样不落盘。
  - 理由:`verify.py` 回放重建行,瞬态故障在那个区块**不可复现**——一旦落盘,该轮链上承诺永久无法复核(违反本节原像可复现原则)。
  - 影响面:**已发布轮次零受影响**(截至 2026-09-11,已发布 12,296 行 + 本地未同步 2,088 行共 14,384 行,100% `ok`)。

## 3. 合约接口

```solidity
// IEvidenceLedger — 证据账本(公证层,非数据仓库)
// SPDX: owner-only commit;任何人可读、可复核

struct CommitRecord {
    bytes32 roundHash;   // 该 block 轮次的 roundKeccak
    uint8   canon;       // canon 版本(2 = rhdepth-v2)
}

mapping(uint64 blockRef => CommitRecord) internal committed;
uint64 public lastCommittedBlock;   // 严格递增水印

function commitBatch(uint64[] calldata blocks, bytes32[] calldata roundHashes) external onlyOwner;
// 约束:blocks 严格递增;两数组等长;逐轮写入一条 CommitRecord

function getRoundHash(uint64 blockRef) external view returns (bytes32, uint8 canon);
function latestCommittedBlock() external view returns (uint64);
```

- **为什么 storage 而不是只发 event**:`getRoundHash()` 是只读 state 函数,读不到 event。0.32 gwei 下 storage 代价可忽略;丢掉读接口 = 丢掉"未来可被消费"叙事。event 仍发(作索引/展示),但不作为真相源。
- 存储布局建议:`CommitRecord` 与 `lastCommittedBlock` 打包,`roundHash`+`canon` 同 slot 或相邻,由实现按 gas 微调。

## 4. 键与顺序

- 唯一键 = **block**(uint64)。roundTs 不进键、不进哈希原像——第三方无法复现(报告可复核性节已排除 ts/latency)。
- 去重/顺序:`require(blocks[i] > lastCommittedBlock)`,严格递增即拒重复、保 append-only 顺序。
- ts 仅作展示:每笔 commitBatch 的 event 可携带范围信息,不参与存储键。

## 5. 不可变性硬约束

- **已发布文件只增不改。** `rounds.jsonl` 在 MANIFEST.sha256 覆盖内;其中 `committed` 字段为采集侧遗留字段,**永不回写**。
- 链 = "哪些轮已提交"的唯一真相(`latestCommittedBlock()` / `getRoundHash()` 查询即答)。
- 本地索引如需镜像,用**独立 append-only** `commits.jsonl`:{block, txHash, committedAt};新文件、新校验和,不碰任何已发布字节。

## 6. 提交模型

- 批量回填,本地短会话集中提交(非实时 relay);每轮哈希来自 VPS manifest `roundKeccak`。
- 批大小 64–128 轮/笔(41 B/轮 calldata);主网全期 ~1,000 轮 ≈ 8–16 笔【09-12 回填实数】。
- 幂等:重跑对已提交 block 直接 revert(§4 严格递增),无重复提交风险。
- commit 成功 + 回执确认后,追加写入本地 `commits.jsonl`(§5),不触碰采集文件。

## 7. canon 版本管理

- `uint8 canonVersion` 随每条记录上链。
- 升级流程:新序列化 = 新 canon 号 + repo 方法论文档 + 新旧对照说明;历史轮次保持原 canon,可验性不随升级失效。
- v1(v1-defective)未上链、不在映射内;映射只含 2 及以上。

## 8. 部署与地址(09-12 后回填)

- 端点(已入本地 `.env`):46630 = `rpc.testnet.chain.robinhood.com`;4663 = `rpc.mainnet.chain.robinhood.com`
- 主网 gas = **ETH**;充值首选 **OKX 直接提币至 4663**(提币网络选 Robinhood Chain 主网;先小额试提 → `cast balance` 验到账);备选 = Arbitrum canonical bridge(portal.arbitrum.io,对所有人开放);所需量小(~0.01–0.05 ETH,含 L1 data fee;批量 41B/轮已最小化 calldata)
- 本地迭代可选 `anvil --fork $RPC_MAINNET`(快、免费、可重放真实状态),但正式部署路径仍为 46630 迭代 → 4663 终版

| 角色 | 链 | 地址 | 备注 |
|---|---|---|---|
| 冒烟测试(V2 前置验证) | 46630 | 【回填】 | 与 iteration ledger 分开,仅权限路径验证 |
| iteration ledger | 46630 | 【回填】 | 提交物公开标注 "iteration ledger, superseded by mainnet" |
| mainnet 正式账本 | 4663 | 【回填】 | 终版;主网回填覆盖 §1 范围 |

- 部署顺序:46630 迭代 → 4663 终版(最终版前不在主网部署其它合约)。
- 私钥仅本地 `.env`;VPS 只拿 ABI/地址,不拿 key。

## 9. 只读边界(公开声明,入 README/demo)

链是公证处,不是数据仓库。`getRoundHash` 回答"这一轮被记录过吗、指纹是什么",不回答"价格是多少"。原始数据在 repo(JSONL + MANIFEST),消费入口在 Web 报告页。

## 10. 排除记录

- **haircut(eventTs):不纳入 v1.0。** 它曾出现在早期草稿,无法追溯设计动机,代码与文档中无对应实现。若日后重新提出,需附明确的失效场景,走 B 区新增流程。
- v1-defective 序列化:不纳入(隔离目录 + 错误档案,报告已公开)。

## 11. demo 口径(冻结时定死)

- C1:verify 闭环只对 RH 线逐轮成立;另两条线为日级校验和,路线图上补——主动说。
- C2:数据早于窗口 = 设计使然(时间序列三周造不出来);窗口内造的是证明层。
- C3:主网逐轮覆盖"从第 1 轮(09-04)到今天"——不是"可验到天"。
- C4(顺序,定死):**发现(QQQ $100 → $6.46 那个数)→ 错误档案(九错,凭什么信你)→ 链上验证(所以不必信我)→ 合约收尾**。合约是句号不是开场白;credibility 要变成 30 秒内看得见的东西,不指望评委自己发现。
- C5(JS 复算闸门):demo 浏览器侧重算哈希须对 Python **100% 轮次一致**才上台;作为 CI 常驻 job(跨语言复算 = 技术硬点的证据)。uint256 一律 BigInt/十进制字符串,防静默错哈希。
- C6(demo 依赖绑定,2026-09-09 落):演示须含**失败路径**——篡改一个字节 → Verify 当场红(与 C4 链上验证同一步;零代码增量,verify.py 现成)。🔴 **"未篡改→绿"依赖链上真有承诺**:截至 09-09 账本为空(226 轮,committed 0/226)——绿镜头依赖 D6 主网回填(9/25)或**测试网 46630 已提交轮次(9/21 起,plan B)**。**C6 继承 D6 风险,与素材清单"早轮真实轮次 verify 镜头"绑在一起排期,不单独排。**
- C7(评委 Q&A 口径):被问"链下源(聚合器报价)怎么补可验证性"→ 两类保证**不同档**:链上派生的深度 = **可独立复现**(任何人用归档节点重算得同一数,哈希只是便利);链下 HTTP 应答 = **仅防篡改**(那次应答无人能重取)。补足路径 = zkTLS/TLSNotary 式应答证明或第三方联签——**路线图回答,不进窗口范围**。
- C8(评委 Q&A 口径,2026-09-09 落):被问"你建在一条正在降温的链上?"→ **① 方法与链无关,RH 只是测试用例;② 衰退期是比健康期更好的测试**(测量基础设施的价值在东西坏掉时显现);③ **不依赖这条链是可当场展示的,不是承诺**——`feed_staleness` 本就跨链(含 7×24 加密对照组),打开即证。

## 12. 页面字段契约(D5 报告页,非链上 —— 冻结时定死,防止范围蔓延)

```
字段名   oracle_staleness_at_snapshot
定义     本页深度快照所钉的区块上,该资产的 Chainlink feed 距上次更新已过去多久;
         并列该 feed 的文档心跳。
形态     ⚠️ 历史事实,不是当前状态。页面是静态发布物——写"此刻陈旧多久"发布次日即为假,
         而本页通篇在讲陈旧,那不只是错,是难堪。
取值     eth_call latestRoundData() @ 快照末区块 → updatedAt;staleness = 快照区块时间 − updatedAt
可行性   ✅ 2026-09-08 实测:归档可回放至区块 54,270,401(3.3M 块前),QQQ/GME/AAPL 3/3 成功
地址源   reference-data-directory feeds-robinhood-mainnet.json(周末 feed 脚本已维护同一 registry,不新增依赖)
⚠️       AMC / RDDT 这条链上没有 feed ⇒ 该列必须支持"no feed on this chain"值,不能留空
         (留空会被读成"陈旧度为 0")
```

呈现建议:**不单独加一行,加成现有深度表的一列**(两事实并排、规则留白给读者):

| Token | $100 | $10k | $100k | Oracle at snapshot block |
|---|---|---|---|---|
| QQQ | 6.46% | 0.26% | 0.03% | 8.2h stale (heartbeat 24h) |
| GME | 99.49% | 97.97% | 17.33% | 0.2h stale (heartbeat 24h) |
| AMC | 99.75% | 97.08% | 74.55% | no feed on this chain |

→ 现有深度表直接变成护栏的输入面,与 SUBMISSION-PACKAGE 定位段"事实/规则/触发"三分法同一张图。

## 附录

- 数据与复算:github.com/dirkdiggler1026/executability-report(code/verify.py、data/)
- 错误档案与隔离目录:报告错误归档节;本 spec §5/§7/§10 均与之对齐
- 旧文档:PRODUCT-INTERFACES-DRAFT.md(superseded,8 token 缺 AMC)
