# RAG 阶段总结与评估记录

> 目的：保留本项目 RAG 阶段的设计思路、正式实验结果、关键问题与最终结论，方便后续复盘、简历撰写与面试准备。
>
> 建议仓库路径：`docs/rag/RAG_EVALUATION_SUMMARY.md`

---

## 1. 当前 RAG 主链路

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

核心配置：

- Final TopK：5（评估同时统计 @1/@3/@5/@10）
- Rerank candidate_top_k：20
- RRF k：60
- HNSW search `ef=64`
- HNSW build：`efConstruction=200, M=16`

---

## 2. 当前知识库规模

```text
Documents: 46
Chunks:    167
```

Chunk 分布：

- 27 个文档：4 chunks
- 18 个文档：3 chunks
- 1 个文档：5 chunks

正式评估采用 chunk-level Ground Truth 为主，document-level 指标作为辅助。

---

## 3. 为什么不仅做 Dense

Dense Vector Retrieval 的核心优势是语义匹配。

```text
Query
  ↓ embedding
Query Vector

Chunk
  ↓ embedding
Chunk Vector

Vector Similarity
  ↓
TopK
```

它不要求 Query 与文档使用完全相同的词，因此更适合同义改写、间接描述、弱关键词问题。

但 Dense 并不意味着关键词检索过时。

BM25 对专有名词、实体、术语、明显关键词等 lexical signal 非常敏感，因此在部分查询上会明显强于 Dense。

结论：

> Dense 与 BM25 不是替代关系，而是互补关系。

---

## 4. Basic / Mixed Benchmark

正式基础集：

```text
80 queries
46 sources covered
```

题型：

```text
keyword       22
detail        15
semantic      17
descriptive   14
fine_grained   9
confusable     3
```

难度标签：

```text
easy    22
medium  46
hard    12
```

注意：

> 人工标注的 difficulty 并不等于真实检索难度。

检索难度还受到关键词重叠、文档相似度、Chunk 分布与语义表达影响。

---

## 5. Basic Benchmark 正式结果

| Strategy | Recall@1 | Recall@3 | Recall@5 | Hit@1 | Hit@3 | Hit@5 | MRR | nDCG@5 | Avg Latency | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | 45.42% | 92.08% | 98.33% | 90.00% | 97.50% | 98.75% | 0.9365 | 0.9332 | 66.20 ms | 75.09 ms |
| BM25 | 50.42% | 97.08% | 100.00% | 96.25% | 100.00% | 100.00% | 0.9771 | 0.9702 | 2.91 ms | 3.97 ms |
| Hybrid | 46.67% | 97.92% | 100.00% | 92.50% | 100.00% | 100.00% | 0.9604 | 0.9614 | 66.20 ms | 73.81 ms |
| Hybrid + Rerank | 51.04% | 98.75% | 100.00% | 97.50% | 100.00% | 100.00% | 0.9854 | 0.9851 | 426.59 ms | 460.31 ms |

对应正式报告：

```text
002_rag_dense_20261009_165200.json
003_rag_hybrid_20261009_171700.json
004_rag_bm25_20261009_180102.json
005_rag_hybrid_rerank_20261009_180942.json
```

---

## 6. Basic Benchmark 得到的主要结论

### Dense 已经很强，但 Benchmark 偏容易

Dense：

```text
Hit@1    = 90%
Recall@5 = 98.33%
```

说明基础集对当前系统已经接近饱和。

这不是单纯的“模型很好”，还说明：

> 如果 Benchmark 太简单，所有方案都接近满分，就失去了区分能力。

因此后续不能继续只在 Basic Benchmark 上调参数，否则容易变成对测试集调参。

### BM25 在 Basic Benchmark 上非常强

BM25：

```text
Hit@1 = 96.25%
MRR   = 0.9771
```

高于 Dense。

但不能得出：

```text
BM25 > Dense
```

只能得出：

> 当前 Basic Benchmark 中存在较多明显 lexical signal，因此 BM25 非常有效。

### Hybrid 相比 Dense 提升明显，但不保证击败最强单路

Dense → Hybrid：

```text
Recall@3: 92.08% → 97.92%
Hit@1:    90.00% → 92.50%
MRR:      0.9365 → 0.9604
```

Hybrid 有明显收益。

但是 Hybrid 没有超过 Basic Benchmark 上的 BM25。

因此：

> Fusion 的目标是增强整体鲁棒性，而不是保证每个 Query 都优于最佳单路 Retriever。

### Reranker 主要改善 Ranking，而不是扩大召回范围

Hybrid → Hybrid + Rerank：

```text
Hit@1:    92.50% → 97.50%
MRR:      0.9604 → 0.9854
nDCG@5:   0.9614 → 0.9851
Recall@5: 100%   → 100%
```

因此：

> Retriever 主要负责把正确证据召回；Reranker 更主要负责把正确证据排到更靠前的位置。

### Reranker 有明显延迟成本

```text
Hybrid Avg Latency          ≈ 66 ms
Hybrid + Rerank Avg Latency ≈ 427 ms
```

大约增加到 6～7 倍。

工程上必须考虑 Quality / Latency / Compute Cost。

---

## 7. 为什么增加 Hard Benchmark

Basic Benchmark 已接近饱和，因此增加 30 条 Hard Query：

```text
paraphrase_semantic  6
confusable           6
indirect_reference   6
fine_grained_hard    6
multi_clue           6
```

目的不是“让分数低”，而是让不同 Retrieval Strategy 的能力差异真正显现出来。

---

## 8. Hard Benchmark 正式结果

| Strategy | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Hit@1 | Hit@3 | Hit@5 | MRR | nDCG@5 | Avg Latency | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | 31.94% | 60.28% | 85.00% | 92.22% | 70.00% | 86.67% | 93.33% | 0.7928 | 0.7278 | 70.60 ms | 107.04 ms |
| BM25 | 34.17% | 64.72% | 72.50% | 80.56% | 63.33% | 90.00% | 90.00% | 0.7593 | 0.6649 | 8.85 ms | 17.85 ms |
| Hybrid | 40.28% | 77.50% | 82.78% | 90.00% | 80.00% | 100.00% | 100.00% | 0.8833 | 0.7880 | 63.88 ms | 78.05 ms |
| Hybrid + Rerank | 44.72% | 84.17% | 90.00% | 96.67% | 90.00% | 96.67% | 96.67% | 0.9367 | 0.8720 | 453.98 ms | 520.54 ms |

对应正式报告：

```text
001_rag_dense_20261009_191417.json
002_rag_bm25_20261009_191419.json
003_rag_hybrid_20261009_191423.json
004_rag_hybrid_rerank_20261009_191445.json
```

---

## 9. Basic vs Hard：最重要的一张表

### Hit@1

| Strategy | Basic 80 | Hard 30 | 变化 |
|---|---:|---:|---:|
| Dense | 90.00% | 70.00% | -20.00pp |
| BM25 | 96.25% | 63.33% | -32.92pp |
| Hybrid | 92.50% | 80.00% | -12.50pp |
| Hybrid + Rerank | 97.50% | 90.00% | -7.50pp |

这张表是整个 RAG 检索阶段最重要的结果之一。

它说明：

```text
Basic：
BM25 已经非常强

Hard：
Dense > BM25
Hybrid > Dense
Rerank > Hybrid
```

Hard Benchmark 更能体现复杂语义检索情况下 Hybrid 与 Reranker 的价值。

---

## 10. Hard Benchmark 各方法的核心解释

### Dense

```text
Hit@1: 70%
MRR:   0.7928
```

相比 Basic 明显下降。说明语义模型虽然能处理改写，但仍会受到相似文档、细粒度区分与间接描述影响。

### BM25

```text
Hit@1: 63.33%
MRR:   0.7593
```

在 Hard Benchmark 上低于 Dense。说明强同义改写、间接描述、弱关键词情况下，纯 lexical matching 的局限开始体现。

但 BM25 仍然不是无效：

```text
Recall@3: BM25 64.72%
Recall@3: Dense 60.28%
```

### Hybrid

```text
Hit@1:    80%
MRR:      0.8833
Recall@3: 77.5%
```

相对 Dense：

```text
Hit@1:    70% → 80%
MRR:      0.7928 → 0.8833
Recall@3: 60.28% → 77.50%
```

复杂 Query 中 Dense 与 BM25 的失败模式不同，二者互补性真正体现出来。

### Hybrid + Rerank

```text
Hit@1:  90%
MRR:    0.9367
nDCG@5: 0.8720
```

相对 Hybrid：

```text
Hit@1:  80% → 90%
MRR:    0.8833 → 0.9367
nDCG@5: 0.7880 → 0.8720
```

因此：

> Reranker 对复杂 Query 的 Top Ranking 质量有明显提升。

代价：

```text
Hybrid Avg ≈ 64 ms
Rerank Avg ≈ 454 ms
```

---

## 11. RRF 应该怎么理解

Dense 与 BM25 的 score 不在同一量纲，不能直接相加比较。

RRF 使用排名进行融合：

```text
RRF Score = Σ 1 / (k + rank)
```

当前：

```text
k = 60
```

关键认识：

> RRF 是一种 rank fusion 方法，不保证融合结果在每个 Query 上都超过最佳单路结果。

它追求的是跨 Query 的鲁棒性。

---

## 12. Recall、Hit、MRR、nDCG 的区别

假设：

```text
Relevant = [A, B, C]
Top5     = [X, A, Y, Z, W]
```

### Hit@5

```text
Hit@5 = 1
```

回答：

> 有没有找到至少一个正确证据？

### Recall@5

```text
Recall@5 = 1 / 3
```

回答：

> 应该找到的相关证据，一共找回了多少？

### MRR

```text
rank1  → RR = 1
rank2  → RR = 0.5
rank3  → RR = 0.333
rank10 → RR = 0.1
```

回答：

> 第一个正确证据出现得够不够早？

### nDCG

会进一步关心多个 relevant chunk 的整体排序。

回答：

> 多个相关证据在 TopK 中整体排得好不好？

---

## 13. 为什么 Chunk-level 是主指标

RAG 真正交给 LLM 的通常不是整个 Document，而是 Chunk。

因此：

```text
正确 Document 找到了
```

不等于：

```text
正确 Evidence Chunk 找到了
```

当前评估原则：

```text
Chunk-level = Primary
Document-level = Auxiliary
```

---

## 14. Reranker Failure Case：hard_009

Query 核心区分条件：

```text
同样保存大量佛教造像艺术，
但哪一处还保存大量古代写本文献？
```

Hybrid 已经将正确莫高窟 Chunk 排到：

```text
original_rank = 1
```

Reranker：

```text
reranker_score = 0.258799
final_rank     = 10
```

错误龙门石窟 Chunk：

```text
original_rank = 2
reranker_score = 0.928424
final_rank     = 1
```

说明：

> Reranker 是统计模型，不是规则系统，它只能在整体上改善 Ranking，不保证每一个 Query 都单调变好。

---

## 15. Knowledge Base 本身也会影响 Retrieval

Failure Trace 中发现不同文档存在较多模板化表达，例如：

```text
长期演变
文化交流
保护与研究
社会意义
宗教信仰
区域交流
```

这会增加：

```text
semantic noise
```

因此 RAG Quality 不仅取决于：

```text
Embedding
Retriever
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

## 16. 本阶段最终应该记住的结论

1. Dense 与 BM25 是互补关系，不是替代关系。
2. BM25 在关键词、实体、专有名词明显的场景可以非常强。
3. Dense 在语义改写、间接描述、弱关键词问题上更有优势。
4. Hybrid/RRF 的核心价值是提高整体鲁棒性，不保证每个 Query 都超过最佳单路。
5. RRF 通过排名融合解决 Dense score 与 BM25 score 不同量纲的问题。
6. Retriever 更主要负责 Recall，Reranker 更主要负责 Ranking。
7. Reranker 能提高 Hit@1、MRR、nDCG，但显著增加延迟。
8. Reranker 不是单调改善器，也可能发生错误重排。
9. Benchmark 太简单会导致所有方法接近满分，从而失去区分能力。
10. RAG 效果不仅由检索模型决定，也受知识库、Chunk 与 Ground Truth 质量影响。

---

## 17. 当前阶段正式结论

当前 Retrieval Pipeline：

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

在 Hard Benchmark 中：

```text
Dense Hit@1             = 70%
BM25 Hit@1              = 63.33%
Hybrid Hit@1            = 80%
Hybrid + Rerank Hit@1   = 90%
```

证明：

> 在复杂 Query 下，Dense 与 BM25 具有明显互补性，RRF Hybrid 能增强召回与鲁棒性；在此基础上加入 Reranker 可以进一步提高 Top Ranking Quality，但同时引入明显的延迟成本。

---

## 18. 面试时不要过度表述

不要说：

```text
“我的 RAG 准确率达到 98%”
```

推荐表达：

> 我构建了 80 条 Mixed Benchmark 和 30 条 Hard Benchmark，对 Dense、BM25、RRF Hybrid 和 Hybrid+Rerank 进行了统一离线评估。在 Hard Benchmark 上，Hybrid 相比 Dense 将 Hit@1 从 70% 提升到 80%，MRR 从 0.793 提升到 0.883；加入 Reranker 后 Hit@1 达到 90%、MRR 达到 0.937，但平均检索延迟从约 64ms 增加到约 454ms，因此最终需要根据质量与延迟需求选择链路。

---

## 19. 建议保留的正式实验文件

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

Markdown 文件负责“人读”，JSON 报告负责“数据证据”。

---

## 20. 项目目录建议

```text
Cultural heritage ai/
│
├─ docs/
│  └─ rag/
│     └─ RAG_EVALUATION_SUMMARY.md
│
├─ evaluation/
│  ├─ datasets/
│  │  ├─ rag_eval_v1.json
│  │  └─ rag_eval_hard_v1.json
│  ├─ runners/
│  ├─ metrics/
│  └─ reports/
│     ├─ basic_benchmark/
│     └─ hard_benchmark_v1/
│
└─ app/
   └─ modules/
      └─ rag/
```

`docs/rag/` 负责知识沉淀；`evaluation/` 负责可执行代码、数据集与原始结果。

---

## 21. 下一阶段

当前阶段解决的是：

```text
Retrieval Quality
```

下一阶段进入：

```text
Answer Quality
```

比较：

```text
LLM Only
vs
LLM + RAG
vs
LLM + RAG + Rerank
```

进一步评估：

```text
Answer Correctness
Faithfulness
Hallucination
Completeness
Evidence Support
```

最终要回答：

```text
Retrieval Quality
        ↓
是否能够真正转化为
        ↓
Answer Quality
```

这一步完成后，才形成完整的 End-to-End RAG Evaluation。
