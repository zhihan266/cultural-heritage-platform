from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "datasets"
    / "rag_eval_v1.json"
)

OUTPUT_PATH = (
    PROJECT_ROOT
    / "evaluation"
    / "datasets"
    / "rag_eval_hard_v1.json"
)


# ---------------------------------------------------------
# 每一条 hard case 都从已经审核过的 v1 case 派生。
#
# base_ids:
#   用于继承 Ground Truth。
#
# 如果有多个 base_id：
#   relevant_chunk_ids 会取并集。
#
# query:
#   故意减少原文直接关键词，
#   或增加混淆、间接表达、多个条件。
# ---------------------------------------------------------

HARD_CASES = [

    # =====================================================
    # 1. Paraphrase / Semantic
    # 尽量不直接照搬知识库措辞
    # =====================================================

    {
        "base_ids": ["eval_010"],
        "query":
            "一种圆形或方形大型聚居建筑，其厚实土质围护结构"
            "为什么既关系到建筑稳定，又能应对外部安全威胁？",
        "category": "paraphrase_semantic",
        "reference_answer": "福建土楼的夯土外墙兼具结构支撑和防御作用",
    },

    {
        "base_ids": ["eval_071"],
        "query":
            "古代欧亚交流网络为什么不能理解为从起点直达终点的"
            "一条固定道路，而更适合看作相互连接的交通体系？",
        "category": "paraphrase_semantic",
        "reference_answer": "丝绸之路由多条陆路及多个交通节点共同组成",
    },

    {
        "base_ids": ["eval_073"],
        "query":
            "传统园林在实际占地没有增加的情况下，"
            "怎样借助园外或远处景物使游览者产生空间被扩展的感觉？",
        "category": "paraphrase_semantic",
        "reference_answer": "通过借景扩大有限空间的视觉范围",
    },

    {
        "base_ids": ["eval_075"],
        "query":
            "同一类大型陶制人物为什么能够成批制造，"
            "同时不同个体的面貌和细节又不会完全相同？",
        "category": "paraphrase_semantic",
        "reference_answer": "采用模块化生产并结合人工修整",
    },

    {
        "base_ids": ["eval_076"],
        "query":
            "同样以采摘后的植物嫩叶为原料，"
            "为什么最终可以形成风味和性质不同的多种饮品？",
        "category": "paraphrase_semantic",
        "reference_answer": "鲜叶处理方式不同，杀青、揉捻、发酵、焙制等工序随茶类变化",
    },

    {
        "base_ids": ["eval_078"],
        "query":
            "有一处道教建筑遗产并没有强行把山地改造成规整平地，"
            "其建筑组织方式主要体现了怎样的人与环境关系？",
        "category": "paraphrase_semantic",
        "reference_answer": "武当山古建筑群强调顺应自然地形进行布局",
    },


    # =====================================================
    # 2. Confusable
    # 和相似文化遗产形成竞争
    # =====================================================

    {
        "base_ids": ["eval_060"],
        "query":
            "不是福建以夯土形成封闭聚居空间的传统民居，"
            "而是吸收海外建筑因素、并常以较高建筑承担防卫和观察功能的"
            "近代乡村建筑遗产是什么？",
        "category": "confusable",
        "reference_answer": "开平碉楼与村落",
    },

    {
        "base_ids": ["eval_063"],
        "query":
            "同为大型佛教石窟遗产，如果重点依据北朝到唐代之间"
            "造像风格的连续变化来判断，最符合的是哪一处？",
        "category": "confusable",
        "reference_answer": "龙门石窟",
    },

    {
        "base_ids": ["eval_064"],
        "query":
            "同样保存大量佛教造像艺术，但有一处遗产还因为保存了"
            "大量古代写本文献，使研究价值不限于雕塑和绘画，它是哪一处？",
        "category": "confusable",
        "reference_answer": "莫高窟",
    },

    {
        "base_ids": ["eval_052"],
        "query":
            "如果要从中国石刻遗产中寻找一个"
            "佛教、道教与儒家题材同时出现的实例，应首先想到哪一处？",
        "category": "confusable",
        "reference_answer": "大足石刻",
    },

    {
        "base_ids": ["eval_048"],
        "query":
            "有一处皇家园林遗产周围还分布着一组体现多民族宗教建筑"
            "交流的寺庙群，这一描述指向哪里？",
        "category": "confusable",
        "reference_answer": "承德避暑山庄及周围寺庙",
    },

    {
        "base_ids": ["eval_068"],
        "query":
            "不是以陆路商贸网络著称，而是一座在宋元时期因海上贸易繁荣、"
            "并留下多种宗教遗存以反映人口来源复杂性的城市遗产是什么？",
        "category": "confusable",
        "reference_answer": "泉州：宋元中国的世界海洋商贸中心",
    },


    # =====================================================
    # 3. Indirect Reference
    # 不直接出现遗产名称和最典型实体名称
    # =====================================================

    {
        "base_ids": ["eval_054"],
        "query":
            "哪项古代工程的核心思路不是筑起一道高坝把河水彻底拦住，"
            "而是利用原有水势完成分流、排沙和灌溉？",
        "category": "indirect_reference",
        "reference_answer": "都江堰",
    },

    {
        "base_ids": ["eval_061"],
        "query":
            "哪处佛教遗产不是在独立建筑中塑造巨像，"
            "而是直接依托山体开凿，并与两河交汇附近的自然环境形成整体景观？",
        "category": "indirect_reference",
        "reference_answer": "乐山大佛",
    },

    {
        "base_ids": ["eval_067"],
        "query":
            "如果研究明清时期中国城市格局，同时又希望观察传统商业和"
            "早期金融活动的历史痕迹，哪座保存较完整的古城尤其具有价值？",
        "category": "indirect_reference",
        "reference_answer": "平遥古城",
    },

    {
        "base_ids": ["eval_065"],
        "query":
            "哪处山岳景观常因气象快速变化，让游客即使站在同一地点，"
            "前后看到的山峰与云雾效果也明显不同？",
        "category": "indirect_reference",
        "reference_answer": "黄山",
    },

    {
        "base_ids": ["eval_077"],
        "query":
            "哪处以湖泊、堤岸和人文景观共同构成的文化景观，"
            "其山水审美后来对东亚园林产生了广泛影响？",
        "category": "indirect_reference",
        "reference_answer": "杭州西湖文化景观",
    },

    {
        "base_ids": ["eval_057"],
        "query":
            "哪项跨越多个地区的古代水运工程，"
            "其历史价值长期与国家粮食运输和区域经济联系紧密相关？",
        "category": "indirect_reference",
        "reference_answer": "中国大运河",
    },


    # =====================================================
    # 4. Fine-grained Hard
    # 必须定位到正确文档内部的具体 chunk
    # =====================================================

    {
        "base_ids": ["eval_055"],
        "query":
            "同一皇家宫殿群内部，负责重大礼仪和政务的空间"
            "与皇室日常居住空间，在功能分区上分别偏向什么？",
        "category": "fine_grained_hard",
        "reference_answer": "外朝偏重典礼与政务，内廷偏重起居",
    },

    {
        "base_ids": ["eval_058"],
        "query":
            "在一套由墙体、观察报警设施和军事节点共同组成的防御网络中，"
            "真正用于扼守道路和通行位置的主要是哪类设施？",
        "category": "fine_grained_hard",
        "reference_answer": "关隘",
    },

    {
        "base_ids": ["eval_059"],
        "query":
            "一种传统弦乐器可以通过空弦、按弦以及轻触特定位置产生"
            "三类性质不同的声音，这三类声音分别是什么？",
        "category": "fine_grained_hard",
        "reference_answer": "散音、按音、泛音",
    },

    {
        "base_ids": ["eval_069"],
        "query":
            "某儒家纪念性建筑群中，"
            "哪一部分并非主要承担祭祀活动，而更多与后裔的居住和事务管理有关？",
        "category": "fine_grained_hard",
        "reference_answer": "孔府",
    },

    {
        "base_ids": ["eval_072"],
        "query":
            "一座皇家园林中，一处长距离建筑主要负责连接多个景观节点，"
            "另一座高处建筑则形成重要视觉焦点，它们分别承担什么空间作用？",
        "category": "fine_grained_hard",
        "reference_answer": "长廊串联景观节点，佛香阁形成视觉中心",
    },

    {
        "base_ids": ["eval_049"],
        "query":
            "欣赏一件传统文字艺术作品时，"
            "如果分别讨论单个字内部部件关系和整幅作品中字行之间的安排，"
            "对应的是哪两个层次？",
        "category": "fine_grained_hard",
        "reference_answer": "结体关注单字内部结构，章法关注整幅行列与呼应",
    },


    # =====================================================
    # 5. Multi-clue / Cross-chunk
    #
    # 使用同一文档的两条旧 Ground Truth，
    # relevant_chunk_ids 自动取并集。
    # =====================================================

    {
        "base_ids": ["eval_012", "eval_058"],
        "query":
            "某线性军事防御体系中，一类设施负责快速传递敌情，"
            "另一类设施负责把守重要交通通道。"
            "这两种功能共同指向哪项遗产？",
        "category": "multi_clue",
        "reference_answer": "长城",
    },

    {
        "base_ids": ["eval_009", "eval_055"],
        "query":
            "某皇家宫殿群既存在以三座主要大殿为核心的礼仪政务区域，"
            "又把皇室生活区域与其明确分开。"
            "这一空间组织描述的是哪项遗产？",
        "category": "multi_clue",
        "reference_answer": "故宫",
    },

    {
        "base_ids": ["eval_008", "eval_054"],
        "query":
            "某古代水利工程既利用一个关键结构把河流分成不同水路，"
            "整体设计又不是依靠高坝彻底截断河流。"
            "这项工程是什么？",
        "category": "multi_clue",
        "reference_answer": "都江堰",
    },

    {
        "base_ids": ["eval_028", "eval_068"],
        "query":
            "一座古代港口城市既通过桥梁加强港城与内陆腹地的联系，"
            "又留下多种宗教遗存反映外来人口与文化交流。"
            "这是哪座城市？",
        "category": "multi_clue",
        "reference_answer": "泉州",
    },

    {
        "base_ids": ["eval_017", "eval_062"],
        "query":
            "某史前文明一方面修建了大型水利设施，显示出组织公共工程的能力，"
            "另一方面又以具有礼仪性质的玉器体现社会秩序。"
            "对应的是哪处遗址？",
        "category": "multi_clue",
        "reference_answer": "良渚古城遗址",
    },

    {
        "base_ids": ["eval_032", "eval_071"],
        "query":
            "某古代跨区域交流体系既不是一条单独道路，"
            "又依靠古城、驿站等节点组织长距离往来。"
            "这一描述对应什么遗产？",
        "category": "multi_clue",
        "reference_answer": "丝绸之路：长安—天山廊道",
    },
]


def unique(values: list[str]) -> list[str]:
    """保持原顺序去重。"""
    return list(dict.fromkeys(values))


def main() -> None:
    raw_cases = json.loads(
        SOURCE_PATH.read_text(
            encoding="utf-8"
        )
    )

    cases_by_id = {
        item["id"]: item
        for item in raw_cases
    }

    result = []

    for index, spec in enumerate(
        HARD_CASES,
        start=1,
    ):
        base_cases = []

        for base_id in spec["base_ids"]:
            if base_id not in cases_by_id:
                raise KeyError(
                    f"Unknown base case: {base_id}"
                )

            base_cases.append(
                cases_by_id[base_id]
            )

        relevant_sources = unique([
            source
            for case in base_cases
            for source in case[
                "relevant_sources"
            ]
        ])

        relevant_document_ids = unique([
            document_id
            for case in base_cases
            for document_id in case[
                "relevant_document_ids"
            ]
        ])

        relevant_chunk_ids = unique([
            chunk_id
            for case in base_cases
            for chunk_id in case[
                "relevant_chunk_ids"
            ]
        ])

        result.append({
            "id": f"hard_{index:03d}",
            "query": spec["query"],
            "category": spec["category"],
            "difficulty": "hard",

            "relevant_sources":
                relevant_sources,

            "relevant_document_ids":
                relevant_document_ids,

            "relevant_chunk_ids":
                relevant_chunk_ids,

            "reference_answer":
                spec["reference_answer"],

            # runner 当前会忽略这两个附加字段，
            # 但保留下来方便人工审计。
            "derived_from":
                spec["base_ids"],

            "benchmark_version":
                "hard_v1",
        })

    OUTPUT_PATH.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Generated {len(result)} hard cases"
    )

    print(
        f"Saved to: {OUTPUT_PATH}"
    )

    print("\nCategory counts:")

    categories = {}

    for item in result:
        category = item["category"]
        categories[category] = (
            categories.get(category, 0)
            + 1
        )

    for category, count in categories.items():
        print(
            f"  {category}: {count}"
        )


if __name__ == "__main__":
    main()