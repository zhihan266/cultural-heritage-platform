# RAG 面试复习笔记

> 建议项目路径：`docs/rag/RAG_INTERVIEW_REVIEW.md`
>
> 用途：面试前快速复习。重点不是记所有细节，而是能把“为什么这样设计、数据说明什么、哪里有 trade-off”讲清楚。

---

## 1. 30 秒介绍我的 RAG

我的 RAG 检索链路不是只做向量搜索，而是：

```text
Dense + BM25
→ RRF Hybrid
→ Candidate Top20
→ Reranker
→ Final TopK
```

我另外做了一套离线 Retrieval Evaluation，对 Dense、BM25、Hybrid 和 Hybrid+Rerank 进行统一评估。

一共构建：

```text
80 条 Mixed Benchmark
30 条 Hard Benchmark
```

评估指标包括：

```text
Recall@K
Hit@K
MRR
nDCG
Average Latency
P95 Latency
```

Hard Benchmark 上：

```text
Dense Hit@1           = 70%
BM25                   = 63.33%
Hybrid                 = 80%
Hybrid + Rerank        = 90%
```

同时 Reranker 平均延迟从约 64ms 增加到约 454ms，所以最后结论不是“Rerank 一定最好”，而是需要在质量和延迟之间权衡。

---

## 2. 为什么同时使用 Dense 和 BM25？

推荐回答：

> Dense 擅长语义匹配，即使 Query 与文档词面不一致，只要语义接近也可能召回；BM25 更擅长实体、专有名词和明确关键词。两者失败模式不同，所以我用 Hybrid 做互补。

一句话：

```text
Dense = semantic signal
BM25  = lexical signal
```

---

## 3. 为什么不用纯 Dense？

因为实验结果证明 BM25 在部分 Query 上非常强。

Basic Benchmark：

```text
Dense Hit@1 = 90%
BM25 Hit@1  = 96.25%
```

这说明：

> Embedding 并不会让关键词检索失去价值。

但 Hard Benchmark：

```text
Dense Hit@1 = 70%
BM25 Hit@1  = 63.33%
```

说明语义改写、间接表达多了以后，Dense 的优势才更明显。

---

## 4. 为什么 Hybrid 不一定比 BM25 强？

因为 Fusion 不是“取两边最优结果”。

如果：

```text
BM25 把正确结果排 rank1
Dense 把错误但语义相近结果排 rank1
```

RRF 会综合两边排名，最终可能让错误候选获得更多权重。

所以：

> Hybrid 的目标是提高跨 Query 的整体鲁棒性，而不是保证每个 Query 都超过最佳单路 Retriever。

---

## 5. RRF 是什么？

核心公式：

```text
RRF Score = Σ 1 / (k + rank)
```

当前：

```text
k = 60
```

为什么使用 RRF？

因为：

```text
Dense similarity score
BM25 score
```

不在同一数值尺度。

RRF 直接融合 ranking，不依赖 score 的绝对大小。

---

## 6. Retriever 和 Reranker 的区别

Retriever：

```text
目标：召回
问题：正确结果能不能进入 candidate set？
```

Reranker：

```text
目标：排序
问题：已经召回的 candidate 谁应该排前面？
```

实验：

```text
Hybrid Hit@1  = 80%
Rerank Hit@1  = 90%

Hybrid MRR    = 0.883
Rerank MRR    = 0.937
```

所以 Reranker 的主要贡献是：

```text
Ranking Quality
```

---

## 7. 为什么 Reranker 没让 Recall@K 一直大涨？

因为 Reranker 本质上不负责发现新的文档。

流程：

```text
Retriever
→ Top20 candidates
→ Reranker
```

Reranker 只能重新排列 Candidate。

如果正确文档根本没进入 Top20：

```text
Reranker 也救不回来
```

---

## 8. Recall、Hit、MRR、nDCG 怎么区别？

### Hit@K

```text
TopK 中有没有至少一个正确 Evidence？
```

### Recall@K

```text
所有正确 Evidence 中，有多少被找回？
```

### MRR

```text
第一个正确 Evidence 排得多靠前？
```

### nDCG

```text
多个正确 Evidence 在整个 TopK 中整体排得好不好？
```

快速记忆：

```text
Hit    = 有没找到
Recall = 找全多少
MRR    = 第一个多靠前
nDCG   = 整体排序好不好
```

---

## 9. 为什么 Chunk-level 比 Document-level 更重要？

因为 RAG 最终送给 LLM 的通常是 Chunk。

可能出现：

```text
Document 找对了
但具体 Chunk 找错了
```

这时 LLM 仍然拿不到真正 Evidence。

所以我最终：

```text
Chunk-level = Primary
Document-level = Auxiliary
```

---

## 10. 为什么需要 Hard Benchmark？

第一版 80 条 Basic Benchmark 已经接近饱和：

```text
Dense Recall@5   ≈ 98%
BM25 Recall@5    = 100%
Hybrid Recall@5  = 100%
```

如果所有方案都接近满分，就很难判断优化是否真实有效。

所以我另外构建了 30 条 Hard Query：

```text
paraphrase_semantic
confusable
indirect_reference
fine_grained_hard
multi_clue
```

这套数据才真正拉开：

```text
BM25
Dense
Hybrid
Rerank
```

之间的能力差异。

---

## 11. 最重要的一张数据表

### Hit@1

| Strategy | Basic | Hard |
|---|---:|---:|
| Dense | 90% | 70% |
| BM25 | 96.25% | 63.33% |
| Hybrid | 92.5% | 80% |
| Hybrid + Rerank | 97.5% | 90% |

解释：

```text
Basic：
关键词信号比较明显，因此 BM25 很强。

Hard：
语义改写和混淆增加后，
Dense 优于 BM25，
Hybrid 再超过 Dense，
Rerank 最终最高。
```

---

## 12. Reranker 的代价是什么？

Hard Benchmark：

```text
Hybrid Avg Latency        ≈ 64ms
Hybrid+Rerank Avg Latency ≈ 454ms
```

约增加：

```text
7 倍
```

因此真实工程中不能只看质量。

要考虑：

```text
质量
延迟
吞吐
计算成本
```

---

## 13. Reranker 会不会排错？

会。

代表 Case：

```text
hard_009
```

Hybrid：

```text
正确莫高窟 Chunk = rank1
```

Rerank：

```text
正确 Chunk → rank10
```

而错误龙门石窟 Chunk：

```text
rank2 → rank1
```

说明：

> Reranker 是统计模型，只能整体上提高排序质量，不能保证每一个 Query 都变好。

这也是为什么必须做：

```text
case-level analysis
```

而不能只看平均指标。

---

## 14. hard_009 说明了什么？

Query 的真正区分条件是：

```text
古代写本文献
敦煌文书
```

但错误候选中存在大量：

```text
佛教
造像
艺术
历史
研究
```

等共享语义。

Reranker 可能过度关注主题相似度，而没有充分利用真正负责消歧的特征。

这属于：

```text
semantic misranking
```

---

## 15. Knowledge Base 本身为什么也重要？

多个文档有大量模板化内容：

```text
长期演变
文化交流
保护与研究
社会意义
区域交流
```

会导致：

```text
semantic noise
```

因此 RAG 效果不仅取决于模型，也取决于：

```text
Knowledge Quality
Chunk Quality
Information Density
Document Distinguishability
```

---

## 16. Metadata Enhanced Rerank 实验结果

我尝试：

```text
source/title + chunk
```

作为 Reranker 输入。

结果：

- 部分 Chunk Recall 和 nDCG 略有提升。
- Hit@1 基本不变。
- Document-level 指标出现退化。
- hard_009 仍未真正解决。

因此最终没有把它设置成默认方案，只保留为实验开关。

面试表达：

> 我没有因为单个 Case 改善就直接上线，而是看整体 Benchmark；因为总体收益不足且部分指标退化，所以没有采用。

---

## 17. Basic 和 Hard 为什么结果差这么多？

因为 Basic 很多问题有明确关键词锚点。

Hard 刻意增加：

```text
同义改写
间接描述
相似实体干扰
多线索组合
细粒度区分
```

所以 Hard 更接近真正能区分 Retrieval Strategy 的 Stress Benchmark。

---

## 18. 面试不要说什么？

不要说：

```text
“我的 RAG 准确率 98%”
```

因为：

```text
Hit@1
Recall@5
MRR
nDCG
```

不是同一个概念。

推荐：

> 在 Hard Benchmark 上，Hybrid 相比 Dense 将 Hit@1 从 70% 提升到 80%，MRR 从 0.793 提升到 0.883；加入 Reranker 后 Hit@1 达到 90%、MRR 达到 0.937，但平均检索延迟从约 64ms 增长到约 454ms。

---

## 19. 如果面试官问：为什么最终不是纯 BM25？

回答：

> 在 Basic Benchmark 上 BM25 的确非常强，但 Hard Benchmark 中 Hit@1 从 96.25% 降到 63.33%，而 Dense 是 70%，Hybrid 达到 80%。这说明 BM25 对明确 lexical signal 很强，但复杂语义 Query 下存在明显局限，因此我最终保留 Hybrid。

---

## 20. 如果问：为什么最终不是纯 Dense？

回答：

> Dense 对语义问题更稳定，但 BM25 在实体、专有名词和精确关键词上仍然具有独立价值。Hard Benchmark 中 Hybrid 的 Hit@1 从 Dense 的 70% 提升到 80%，MRR 从 0.793 提升到 0.883，说明两路信号有真实互补性。

---

## 21. 如果问：为什么一定要做评估？

回答：

> 如果没有统一 Benchmark，我只能说“感觉 Hybrid 更好”，没有工程意义。统一数据集和统一指标以后，才能判断每次优化到底改善了 Recall、Ranking 还是只增加了延迟。

---

## 22. 如果问：你觉得这个项目最有价值的点是什么？

推荐回答：

> 不只是实现了 Dense、BM25、RRF 和 Reranker，而是把 Retrieval 做成了可评估系统。我构建了 Mixed 和 Hard 两套 Benchmark，并且对失败 Query 做到了 case-level trace，可以解释某个正确 Chunk 为什么从 Hybrid rank1 被 Reranker 排到 rank10。这让我能基于数据做工程取舍，而不是只堆模块。

---

## 23. 一分钟完整回答模板

> 我的 RAG 检索链路采用 Dense 和 BM25 双路召回，通过 RRF 做 rank fusion，然后对 Top20 candidate 做 rerank。为了验证这套设计，我构建了 80 条 Mixed Benchmark 和 30 条 Hard Benchmark，统一统计 Recall、Hit、MRR、nDCG 和延迟。Basic 集里 BM25 因为关键词信号明显表现非常强，但进入 Hard Benchmark 后，Dense Hit@1 是 70%，BM25 是 63.33%，Hybrid 提升到 80%，说明语义和词法信号确实互补。加入 Reranker 后 Hit@1 提升到 90%、MRR 到 0.937，不过平均延迟从约 64ms 增加到 454ms。我还针对错误重排做了 Trace，发现 Reranker 不是单调改善器，因此最终会根据质量、延迟和失败风险做权衡，而不是简单地认为模型越多越好。

---

## 24. 最后记住这 8 句话

```text
1. Dense 和 BM25 是互补，不是替代。

2. BM25 强不等于它“更智能”，只是 lexical signal 很强。

3. Hybrid 追求鲁棒性，不保证每个 Query 都更好。

4. Retriever 主要负责 Recall。

5. Reranker 主要负责 Ranking。

6. Reranker 有质量收益，也有明显延迟成本。

7. Benchmark 太容易，实验结果就没有区分度。

8. Failure Analysis 比单看平均分更能体现工程能力。
```
