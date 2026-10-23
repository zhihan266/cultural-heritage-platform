# RAG 项目文档：架构、评估与实验结论

> 建议项目路径：`docs/rag/RAG_PROJECT_NOTES.md`
>
> 用途：作为项目正式技术文档，记录当前 RAG 架构、评估方法、正式实验结果、工程结论与后续方向。

---

## 1. 项目目标

本阶段目标不是单纯“把 RAG 跑通”，而是构建一套可解释、可比较、可复现的 Retrieval Evaluation 流程，回答以下问题：

1. Dense、BM25、Hybrid、Hybrid+Rerank 哪种方案更适合当前知识库？
2. 不同检索策略分别擅长什么 Query？
3. Reranker 是否真的提高排序质量？
4. 提升的效果是否值得额外延迟？
5. 评估集是否足够困难，能够区分不同方案？
6. 出现错误时，能否通过 Trace 分析具体原因？

---

## 2. 当前 RAG 检索架构

```text
User Query
   │
   ├───────────────┐
   │               │
   ▼               ▼
Dense Vector      BM25
语义检索           关键词检索
   │               │
   └───────┬───────┘
           ▼
       RRF Fusion
        (k = 60)
           │
           ▼
    Hybrid Candidates
        Top 20
           │
           ▼
       Reranker
           │
           ▼
       Final TopK
           │
           ▼
           LLM
```

关键参数：

```text
candidate_top_k = 20
RRF k           = 60
HNSW ef         = 64
efConstruction  = 200
M               = 16
```

---

## 3. 知识库规模

```text
Documents = 46
Chunks    = 167
```

Chunk 分布：

```text
27 documents × 4 chunks
18 documents × 3 chunks
 1 document  × 5 chunks
```

当前正式评估：

```text
Chunk-level    = Primary
Document-level = Auxiliary
```

原因：最终真正交给 LLM 的是 Chunk，而不是整个 Document。

---

## 4. Retriever 职责划分

### Dense

主要捕获：

```text
semantic similarity
同义改写
弱关键词
间接描述
```

优点：

- 不要求 Query 与文档词面完全一致。
- 对 paraphrase、semantic query 更友好。

局限：

- 相似主题之间可能发生语义混淆。
- 对精确实体、特殊术语不一定优于 BM25。

---

### BM25

主要捕获：

```text
lexical matching
专有名词
实体
关键词
术语
```

优点：

- 快。
- 对实体词和明确关键词非常强。

局限：

- 同义改写、间接表达、弱关键词 Query 容易退化。

结论：

> Dense 和 BM25 是互补关系，不是替代关系。

---

### Hybrid + RRF

RRF 使用排名而不是原始 score 融合：

```text
RRF Score = Σ 1 / (k + rank)
```

原因：

```text
Dense score
与
BM25 score
```

不在同一量纲，不能直接相加。

工程目标：

> 利用 Dense 与 BM25 的互补性，提高跨 Query 的整体鲁棒性。

注意：

> Fusion 不保证每个 Query 都优于最佳单路 Retriever。

---

### Reranker

Retriever 负责：

```text
尽量把正确候选找回来
```

Reranker 负责：

```text
在候选集合中重新判断相关性
并把最相关 Evidence 往前排
```

因此更主要改善：

```text
Hit@1
MRR
nDCG
```

而不是单纯扩大召回范围。

---

## 5. Basic / Mixed Benchmark

规模：

```text
80 queries
46 sources covered
```

类别：

```text
keyword        22
detail         15
semantic       17
descriptive    14
fine_grained    9
confusable      3
```

人工难度：

```text
easy    22
medium  46
hard    12
```

注意：

> difficulty 只是人工标签，并不等于 Retriever 的真实难度。

---

## 6. Basic Benchmark 正式结果

| Strategy | Recall@1 | Recall@3 | Recall@5 | Hit@1 | Hit@3 | Hit@5 | MRR | nDCG@5 | Avg Latency | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | 45.42% | 92.08% | 98.33% | 90.00% | 97.50% | 98.75% | 0.9365 | 0.9332 | 66.20 ms | 75.09 ms |
| BM25 | 50.42% | 97.08% | 100.00% | 96.25% | 100.00% | 100.00% | 0.9771 | 0.9702 | 2.91 ms | 3.97 ms |
| Hybrid | 46.67% | 97.92% | 100.00% | 92.50% | 100.00% | 100.00% | 0.9604 | 0.9614 | 66.20 ms | 73.81 ms |
| Hybrid + Rerank | 51.04% | 98.75% | 100.00% | 97.50% | 100.00% | 100.00% | 0.9854 | 0.9851 | 426.59 ms | 460.31 ms |

正式报告：

```text
002_rag_dense_20261009_165200.json
003_rag_hybrid_20261009_171700.json
004_rag_bm25_20261009_180102.json
005_rag_hybrid_rerank_20261009_180942.json
```

---

## 7. Basic Benchmark 结论

### Dense 已经接近饱和

```text
Hit@1    = 90%
Recall@5 = 98.33%
```

说明：

> Basic Benchmark 对当前系统偏容易。

Benchmark 太容易会导致所有策略都接近满分，失去区分能力。

---

### BM25 在 Basic 集上很强

```text
Hit@1 = 96.25%
MRR   = 0.9771
```

这不代表 BM25 普遍优于 Dense，只说明：

> 当前基础数据包含大量明确 lexical signal。

---

### Hybrid 相比 Dense 有提升

```text
Recall@3: 92.08% → 97.92%
Hit@1:    90.00% → 92.50%
MRR:      0.9365 → 0.9604
```

说明 Dense 与 BM25 存在互补性。

---

### Reranker 明显改善 Ranking

```text
Hit@1:    92.50% → 97.50%
MRR:      0.9604 → 0.9854
nDCG@5:   0.9614 → 0.9851
Recall@5: 100%   → 100%
```

说明：

> Candidate 已经基本召回完整，Reranker 的主要价值是重新排序。

---

### Reranker 延迟成本明显

```text
Hybrid Avg        ≈ 66 ms
Hybrid+Rerank Avg ≈ 427 ms
```

因此必须考虑：

```text
Quality
Latency
Compute Cost
```

---

## 8. 为什么增加 Hard Benchmark

Basic Benchmark 已接近饱和，因此建立：

```text
30 Hard Queries
```

类型：

```text
paraphrase_semantic  6
confusable           6
indirect_reference   6
fine_grained_hard    6
multi_clue           6
```

目的：

> 让不同 Retrieval Strategy 的能力差异真正显现出来。

---

## 9. Hard Benchmark 正式结果

| Strategy | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Hit@1 | Hit@3 | Hit@5 | MRR | nDCG@5 | Avg Latency | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | 31.94% | 60.28% | 85.00% | 92.22% | 70.00% | 86.67% | 93.33% | 0.7928 | 0.7278 | 70.60 ms | 107.04 ms |
| BM25 | 34.17% | 64.72% | 72.50% | 80.56% | 63.33% | 90.00% | 90.00% | 0.7593 | 0.6649 | 8.85 ms | 17.85 ms |
| Hybrid | 40.28% | 77.50% | 82.78% | 90.00% | 80.00% | 100.00% | 100.00% | 0.8833 | 0.7880 | 63.88 ms | 78.05 ms |
| Hybrid + Rerank | 44.72% | 84.17% | 90.00% | 96.67% | 90.00% | 96.67% | 96.67% | 0.9367 | 0.8720 | 453.98 ms | 520.54 ms |

正式报告：

```text
001_rag_dense_20261009_191417.json
002_rag_bm25_20261009_191419.json
003_rag_hybrid_20261009_191423.json
004_rag_hybrid_rerank_20261009_191445.json
```

---

## 10. Basic vs Hard 核心对比

### Hit@1

| Strategy | Basic 80 | Hard 30 | 变化 |
|---|---:|---:|---:|
| Dense | 90.00% | 70.00% | -20.00pp |
| BM25 | 96.25% | 63.33% | -32.92pp |
| Hybrid | 92.50% | 80.00% | -12.50pp |
| Hybrid + Rerank | 97.50% | 90.00% | -7.50pp |

核心现象：

```text
Basic：
BM25 已经非常强

Hard：
Dense > BM25
Hybrid > Dense
Rerank > Hybrid
```

说明：

> Hard Benchmark 更能体现复杂语义检索场景下 Hybrid 与 Reranker 的价值。

---

## 11. Hard Benchmark 结论

### Dense

```text
Hit@1 = 70%
MRR   = 0.7928
```

说明语义检索可以应对改写，但在相似主题、细粒度区分、间接描述下仍会退化。

### BM25

```text
Hit@1 = 63.33%
MRR   = 0.7593
```

说明强同义改写、间接描述、弱关键词场景下 lexical matching 局限明显。

但：

```text
BM25 Recall@3  = 64.72%
Dense Recall@3 = 60.28%
```

说明 BM25 仍然有独立价值。

### Hybrid

```text
Hit@1    = 80%
MRR      = 0.8833
Recall@3 = 77.5%
```

相比 Dense：

```text
Hit@1:    70% → 80%
MRR:      0.7928 → 0.8833
Recall@3: 60.28% → 77.50%
```

说明：

> Dense 与 BM25 的失败模式不同，融合能够提高复杂 Query 下的鲁棒性。

### Hybrid + Rerank

```text
Hit@1  = 90%
MRR    = 0.9367
nDCG@5 = 0.8720
```

相比 Hybrid：

```text
Hit@1:  80% → 90%
MRR:    0.8833 → 0.9367
nDCG@5: 0.7880 → 0.8720
```

说明 Reranker 对复杂 Query 的 Top Ranking 质量有明显提升。

代价：

```text
Hybrid Avg        ≈ 64 ms
Hybrid+Rerank Avg ≈ 454 ms
```

---

## 12. 指标解释

### Hit@K

回答：

> TopK 中是否至少存在一个 relevant chunk？

适合判断：

```text
有没有至少找到一个正确证据
```

### Recall@K

回答：

> 所有 relevant chunks 中，有多少被 TopK 找回？

适合判断：

```text
证据覆盖是否完整
```

### MRR

只重点关注：

```text
第一个 relevant result 的排名
```

例如：

```text
rank1  -> 1
rank2  -> 0.5
rank3  -> 0.333
rank10 -> 0.1
```

### nDCG

进一步考虑：

```text
多个 relevant chunk 在整个 TopK 中的排序位置
```

适合衡量整体 Ranking Quality。

---

## 13. Failure Case：hard_009

Query：

```text
同样保存大量佛教造像艺术，
但有一处遗产还因为保存了大量古代写本文献，
使研究价值不限于雕塑和绘画，它是哪一处？
```

Hybrid：

```text
正确莫高窟 Chunk
original_rank = 1
```

Reranker：

```text
reranker_score = 0.258799
final_rank     = 10
```

错误龙门石窟 Chunk：

```text
original_rank  = 2
reranker_score = 0.928424
final_rank     = 1
```

结论：

> Reranker 是模型，不是规则，不能保证每个 Query 都单调改善。

它可以整体提升 Benchmark，但仍存在局部 semantic misranking。

---

## 14. Knowledge Base 问题

Failure Trace 中发现多个文化遗产文档存在大量模板化表述：

```text
长期演变
区域交流
文化价值
保护与研究
宗教信仰
社会意义
```

这会导致：

```text
semantic noise
```

因此 Retrieval Quality 不只取决于：

```text
Embedding
BM25
RRF
Reranker
```

还取决于：

```text
Knowledge Quality
Chunk Quality
Information Density
Document Distinguishability
```

---

## 15. Metadata Rerank Ablation

尝试：

```text
Baseline:
query + chunk content

Metadata Enhanced:
query + source/title + chunk content
```

结果：

- Chunk Recall / nDCG 有轻微改善。
- Hit@1 基本不变。
- Document-level 指标出现下降。
- hard_009 仍未真正解决。
- 正确莫高窟 Chunk 虽然 relevance score 上升，但错误相似石窟 Chunk 也同步上升。

结论：

> Metadata Enhancement 暂不作为默认生产策略，仅保留为实验开关。

---

## 16. 本阶段最终结论

当前主链路：

```text
Dense
+
BM25
↓
RRF Hybrid
↓
Candidate Top20
↓
Reranker
↓
Final TopK
```

Hard Benchmark：

```text
Dense Hit@1           = 70%
BM25 Hit@1            = 63.33%
Hybrid Hit@1          = 80%
Hybrid+Rerank Hit@1   = 90%
```

可以总结为：

> 在复杂 Query 下，Dense 与 BM25 具有明显互补性；RRF Hybrid 能提升召回与鲁棒性；在此基础上加入 Reranker 能继续提高 Top Ranking Quality，但同时带来显著延迟成本，并且存在局部错误重排风险。

---

## 17. 建议保留的正式实验文件

Basic：

```text
002_rag_dense_20261009_165200.json
003_rag_hybrid_20261009_171700.json
004_rag_bm25_20261009_180102.json
005_rag_hybrid_rerank_20261009_180942.json
```

Hard：

```text
001_rag_dense_20261009_191417.json
002_rag_bm25_20261009_191419.json
003_rag_hybrid_20261009_191423.json
004_rag_hybrid_rerank_20261009_191445.json
```

原则：

> Markdown 负责“人读”，JSON 负责“数据证据”。

---

## 18. 下一阶段

当前阶段已经解决：

```text
Retrieval Quality
```

下一阶段进入：

```text
Answer Quality
```

计划比较：

```text
LLM Only
vs
LLM + RAG
vs
LLM + RAG + Rerank
```

评估：

```text
Correctness
Faithfulness
Hallucination
Completeness
Evidence Support
```

最终目标：

```text
Retrieval Quality
        ↓
是否真正转化为
        ↓
Answer Quality
```
