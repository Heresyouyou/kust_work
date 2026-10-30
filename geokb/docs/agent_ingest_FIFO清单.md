# GeoKB 待清清单（FIFO · 最早积压优先）

> 生成时间：2026-10-29 23:14
>
> **口径（用户 2026-10-23）**：不用清完。存量动态增长，追不完也不需要追完。
> 只按此清单**从最早积压的开始，按顺序往下清**，清掉多少算多少；
> 新下载的论文自动排到队尾，不打断当前顺序。
>
> 排序依据 = `state/papers_index.jsonl` 行号（`ledger_idx`，追加式台账，行号即积压先后）。

## 概览

| 项 | 值 |
|---|---|
| 队列总量 | 2233 |
| P0 | 290 |
| P1 | 7 |
| P2 | 16 |
| P4 | 1905 |
| 本清单条数 | 313（P0+P1+P2） |

> ⚠️ **只有 P0 会增加论文条目数**；P1/P2 只补知识点与分面，论文数不变。
> 汇报时必须分开说，绝不能合并计数。

---

## P0（290 篇）— 无条目 —— 需完整撰写（**只有这类才增加论文数**）

| 序 | 台账行 | sha12 | 期刊 | 年 | 标题 |
|---|---|---|---|---|---|
| 1 | 1947 | `75312de90049` | 地质学报 | 2026 | 四川盆地蓬莱地区茅口组储层流体演化与石油地质意义 |
| 2 | 1949 | `be9f3296cabd` | 地球科学 | 2022 | 西藏中部塔吉冈矿区早白垩世花岗岩成因及地质意义 |
| 3 | 1952 | `edcdd90b4e94` | 地球科学 | 2022 | 海相钾盐矿床溶滤卤水找钾指标体系: ——以川东北天星桥构造寒武系深部地下卤水为例 |
| 4 | 1953 | `9a919019fce9` | 地球科学 | 2022 | 冀东秦家峪中元古界高于庄组锰矿成因：来自矿物学和地球化学的制约 |
| 5 | 1954 | `3d78d2a73217` | 地球科学 | 2022 | 新疆罗布泊富钾卤水矿床异常富集机理 |
| 6 | 1955 | `1cd37fc73b8f` | 地球科学 | 2022 | 实验矿床学的发展现状和前景展望 |
| 7 | 1956 | `504c0f2fbdfe` | 地球科学 | 2022 | 南海北部陆缘结构及构造-岩浆演化 |
| 8 | 1957 | `b9057872fa49` | 地球科学 | 2022 | 煤中关键金属元素的赋存状态与富集机制是什么？ |
| 9 | 1958 | `432ad1bf8258` | 矿床地质 | 2022 | 白云鄂博矿区深边部铁矿床勘查突破及启示 |
| 10 | 1959 | `134bf95313c6` | 地学前缘 | 2022 | 南海北部晚中新世红绿韵律层成因的矿物学和地球化学约束 |
| 11 | 1960 | `7d8490d70fbf` | 地学前缘 | 2022 | 一个新的花岗岩成因分类:基于变质岩深熔作用理论与大数据的证据 |
| 12 | 1961 | `2091f54fd74b` | 地学前缘 | 2022 | 深度挖掘数据潜在价值提高找矿靶区定量优选精度 |
| 13 | 1962 | `34a0544d13bb` | 矿床地质 | 2022 | 东昆仑中东段驼路沟钴（金）矿床钠铁矾的识别及其指示意义 |
| 14 | 1963 | `60833301208b` | 矿床地质 | 2022 | 《矿床地质》2022年总目次 |
| 15 | 1964 | `a7ceb227e46f` | 矿床地质 | 2023 | 新疆西昆仑大红柳滩矿集区含锂伟晶岩脉识别与找矿预测 |
| 16 | 1965 | `e1869663f923` | 矿床地质 | 2023 | 河北省奥陶纪马家沟组顶部古风化壳中三稀元素赋存状态及富集机制 |
| 17 | 1966 | `ae8537ca3ad6` | 地学前缘 | 2023 | 西藏南部冈底斯带东段晚白垩世中性侵入岩的成因矿物学研究:对构建穿地壳岩浆系统的启示 |
| 18 | 1967 | `a4325d42ac81` | 地学前缘 | 2023 | 白云鄂博矿床磁铁矿成分标型与深部富铁矿体预测 |
| 19 | 1968 | `12bfb1b67c61` | 矿床地质 | 2023 | 辽宁青城子地区印支期岩浆作用与找矿预测 |
| 20 | 1969 | `e17f65724faa` | 矿床地质 | 2023 | 柴达木盆地别勒滩杂卤石矿床特征及其沉积环境研究 |
| 21 | 1970 | `072969336322` | 矿床地质 | 2023 | 锡铜共生矿床的研究现状、问题与展望 |
| 22 | 1971 | `b498d00bb070` | 矿床地质 | 2023 | 1982~2022年《矿床地质》载文分析与趋势展望 |
| 23 | 1973 | `33a852b623d3` | 地球科学 | 2024 | 湖北庙垭碳酸岩杂岩体中铌赋存状态及富集机制：矿物化学制约 |
| 24 | 1974 | `793fa8db8f9b` | 地球科学 | 2024 | 俯冲板片稳定同位素(Fe-K-Li-B-Ba)的分馏行为 |
| 25 | 1975 | `5f43e796ade8` | 地球科学 | 2024 | 大陆碰撞带深熔变质与花岗岩成因 |
| 26 | 1976 | `ca460280e651` | 地球科学 | 2024 | 北秦岭松树沟榴闪岩变质演化：石榴石矿物学和锆石年代学制约 |
| 27 | 1977 | `a64a38605e9e` | 地球科学 | 2024 | 俯冲带水岩相互作用: 来自西南天山（超）高压变质岩的矿物学证据 |
| 28 | 1978 | `db6e19e88bb4` | 地质学报 | 2024 | 北羌塘胜利河地区布曲组优质烃源岩形成环境与有机质富集机制 |
| 29 | 1979 | `ed3894410727` | 矿床地质 | 2024 | 宁芜矿集区多阶段火山作用岩浆源区的转变:来自侵入岩锆石Hf-O同位素特征的记录 |
| 30 | 1980 | `d36905b56e02` | 矿床地质 | 2024 | 中国铁矿床伴生关键金属:基本特征、分布规律及资源潜力 |
| 31 | 1981 | `9149e8d0a48d` | 地学前缘 | 2024 | 基于物质来源解析的汕头市土壤重金属环境容量研究 |
| 32 | 1982 | `06496dd6c044` | 地质学报 | 2024 | 科尔沁沙地粗—细组分的碎屑锆石U- Pb年龄特征：对定量物源及区域构造- 岩浆演化事件的指示 |
| 33 | 1983 | `1343f83d6347` | 矿床地质 | 2024 | 西昆仑塔什库尔干新迭铁矿床赋矿地层形成时代及其地质意义 |
| 34 | 1984 | `a831db6e8d8c` | 矿床地质 | 2024 | 当前中国找矿勘查值得关注的问题与发展方向 |
| 35 | 1985 | `ce4123612334` | 矿床地质 | 2024 | 全球风化淋滤型赤铁矿矿床形成条件的讨论——以几内亚西芒杜矿床（Ⅰ、Ⅱ区块）为例 |
| 36 | 2001 | `f398aae15680` | 地质学报 | 2024 | 宁东煤田横城矿区太原组煤中关键金属的物质来源和富集机理 |
| 37 | 2002 | `c329ae0bf6db` | 地质学报 | 2024 | 呵叻高原钾盐矿床空间分布特征与成因及其对钾盐异常富集的影响 |
| 38 | 2003 | `b62c1e79286b` | 地质学报 | 2024 | 中国2024~2035年钾盐供需形势分析：基于矿床地质特征研究 |
| 39 | 2004 | `1ada03f01886` | 地球科学 | 2025 | 南岭半坑高分异A型花岗岩成因及其地质意义 |
| 40 | 2005 | `a9a9c4610890` | 地球科学 | 2025 | 马达加斯加海台地质属性及其构造-岩浆演化过程 |
| 41 | 2006 | `c8d32a526a5e` | 地球科学 | 2025 | 基于广域-编码源电磁测深的白银厂小铁山VHMS型矿床深部找矿模型构建 |
| 42 | 2007 | `f090ce9fe74c` | 地球科学 | 2025 | 大别山南缘翁门杂岩中太古代TTG和钾质花岗岩成因：对扬子陆块早期演化的约束 |
| 43 | 2008 | `25cc7b90c2f5` | 地球科学 | 2025 | 南岭地区正冲锂铷铯矿床云英斑岩成因 |
| 44 | 2009 | `1f57d05c9e2e` | 矿床地质 | 2025 | 基于随机森林的二维找矿预测——以下雷-土湖地区沉积型锰矿为例 |
| 45 | 2010 | `a9651fcfb393` | 地质学报 | 2025 | 中国喀斯特型铝土矿找矿预测研究进展 |
| 46 | 2011 | `6308572d5856` | 矿床地质 | 2025 | 初论矿床动态技术经济评价 |
| 47 | 2012 | `bf9e0e1876a0` | 地质学报 | 2025 | 阿舍勒矿集区VMS型矿床深部勘查模型研究 |
| 48 | 2013 | `21a6c3b6cfe7` | 矿床地质 | 2025 | 湖南观音堂锑矿床地质、地化异常特征及找矿远景分析 |
| 49 | 2014 | `a99d527895d1` | 地质学报 | 2025 | 中国镓锗资源的矿床类型划分、空间分布和开发利用现状 |
| 50 | 2015 | `270d053c0731` | 地质学报 | 2025 | 南岭骑田岭岩体岩浆演化序列与成因 |
| 51 | 2016 | `40342bd9e97b` | 矿床地质 | 2025 | 宁芜盆地钟姑矿田火山岩型铁矿床磁铁矿成因及其地质意义 |
| 52 | 2017 | `c6c8b2b17a80` | 矿床地质 | 2025 | 重庆秀山地区大塘坡式锰矿床沉积环境及成因 |
| 53 | 2018 | `ac1c0db0de8c` | 地学前缘 | 2025 | 中国甘肃金川铜镍(铂)硫化物矿床中金云母类型、成因及其意义 |
| 54 | 2020 | `758503a54a8c` | 地质学报 | 2025 | 藏北安多地区侏罗纪地壳深熔作用与花岗岩成因 |
| 55 | 2021 | `b28c133ef853` | 地质学报 | 2025 | 西藏嘎日阿统超大型花岗岩型铷矿床锆石U- Pb年龄及铷的赋存状态 |
| 56 | 2022 | `bcf16c290475` | 地质学报 | 2025 | 西准噶尔晚石炭世强过铝花岗岩成因及地质意义 |
| 57 | 2023 | `a52c7022fc52` | 地质学报 | 2025 | 桂北摩天岭达亮铀矿床类型再讨论：来自沥青铀矿元素和同位素的证据 |
| 58 | 2024 | `c9232b688138` | 地球科学 | 2026 | 基于大语言模型的华南稀有金属矿床知识图谱构建 |
| 59 | 2025 | `5fe78292ade8` | 地球科学 | 2026 | 基于矿床规模约束的西秦岭寨上-李坝矿集区智能矿产预测 |
| 60 | 2026 | `cbb6021136e7` | 地球科学 | 2026 | 南秦岭武当地区小花果4N8级脉石英型高纯石英矿床的发现及其地质意义 |
| 61 | 2027 | `a69315ed0897` | 地质学报 | 2026 | 基于深度学习的喀斯特型铝土矿找矿预测：以桂西平果地区为例 |
| 62 | 2028 | `5172238012d0` | 矿床地质 | 2026 | 湖南省长平断裂南段盐田桥矿床钴的赋存状态、形成时代及地质意义 |
| 63 | 2029 | `4e3c4f1c483c` | 地学前缘 | 2026 | 银同位素在矿床学中的应用 |
| 64 | 2030 | `9ff2a6f9d886` | 地质学报 | 2026 | 狼山北部高Sr/Y花岗岩成因及其对阿拉善地块晚古生代构造演化的启示 |
| 65 | 2031 | `52c524df0456` | 地学前缘 | 2026 | 地质知识与数据协同驱动的矿床智能预测大数据处理方法综述 |
| 66 | 2032 | `881cbedc8f67` | 地质学报 | 2026 | 大别山地区典型脉石英矿床质量评价方法 |
| 67 | 2033 | `fc93cfa6ab98` | 地球科学 | 2022 | 基于遥感技术估算藏东南山区地下水径流模数方法 |
| 68 | 2034 | `81b4690bb05b` | 地球科学 | 2022 | 大兴安岭南段北大山含锡石与不含锡石花岗岩特征对比 |
| 69 | 2035 | `28c5db7fd279` | 地球科学 | 2022 | 藏东昌都地区首次发现中侏罗世兽脚类恐龙行迹 |
| 70 | 2036 | `6d177352f049` | 地球科学 | 2024 | 滇西点苍山新元古代辉长岩的识别及其大地构造意义 |
| 71 | 2037 | `fa394f02330c` | 地球科学 | 2024 | 藏东南地区直白沟冰雪型地质灾害链时空演化特征 |
| 72 | 2038 | `3182aba7d00d` | 地学前缘 | 2024 | 氦气地质理论认识、资源勘查评价与全产业链一体化评价关键技术 |
| 73 | 2039 | `b8adf5c47bce` | 矿床地质 | 2024 | 2023年新一轮找矿突破战略行动主要科研成果 |
| 74 | 2040 | `7ed31eebc128` | 地学前缘 | 2024 | 冀北山区某有色多金属尾矿库周边农用地重金属污染特征与生态健康风险评价 |
| 75 | 2041 | `9a438bc02e1b` | 地质学报 | 2024 | 西藏南部古堆高温地热田水化学特征及其成因研究 |
| 76 | 2042 | `2f84a27aca15` | 地质学报 | 2024 | 滇西昌宁- 孟连带二叠纪碎屑岩地层：存在问题和研究进展 |
| 77 | 2043 | `62c8b06c239d` | 地学前缘 | 2024 | 开放系统下铅锌对地质碳汇的影响研究 |
| 78 | 2044 | `f407a80ae15e` | 地质学报 | 2024 | 金属稳定同位素示踪关键金属的运移和超常富集 |
| 79 | 2045 | `8aab12332204` | 矿床地质 | 2024 | 《中国矿产地质志》研编历程、重要进展及其在找矿突破战略行动中的应用 |
| 80 | 2046 | `7fbac1f401bc` | 地质学报 | 2024 | 松辽盆地南部辉绿岩侵入热力作用与砂岩铀矿中铀的超常富集 |
| 81 | 2047 | `d4e22dfbcc93` | 地球科学 | 2025 | 藏东南地区冰碛土强度特性对温度响应试验研究 |
| 82 | 2048 | `102d5d4ebf4c` | 地球科学 | 2025 | 江西宜春-新余地区石竹山超大型硅灰石矿区中多金属矿化矽卡岩的特征、形成年代及其找矿意义 |
| 83 | 2049 | `c113fe494098` | 地球科学 | 2025 | 基于斜坡单元与SBAS-InSAR地表形变速率的高山峡谷区滑坡危险性评价：以金沙江上游地巫乡段为例 |
| 84 | 2050 | `e9999299aaff` | 地球科学 | 2025 | 藏东南八宿县林卡乡高位古滑坡群的孕灾条件与形成机制 |
| 85 | 2051 | `d4ca3a5c8ca1` | 地球科学 | 2025 | 藏东南地质灾害的内外动力影响模式研究进展 |
| 86 | 2052 | `c8ebc67ff5d5` | 地球科学 | 2025 | 新疆库拜盆地北缘古河湖相锆石砂矿的发现实现我国锆矿找矿突破 |
| 87 | 2053 | `8001581c310a` | 地球科学 | 2025 | 藏东南则隆弄冰川运动速度长时间季节性变化规律 |
| 88 | 2054 | `b72ad3a08cf9` | 地球科学 | 2025 | 澜沧江上游德钦段大型高位滑坡发育特征与形成演化机制研究 |
| 89 | 2055 | `ec6656877932` | 地球科学 | 2025 | 金沙江上游SBAS-InSAR滑坡三维形变监测与堵江影响分析 |
| 90 | 2056 | `a35acab9fd86` | 地学前缘 | 2025 | 关键元素超常富集与战略资源效应 |
| 91 | 2057 | `375b4dd26252` | 地学前缘 | 2025 | 黄河三角洲地区地下水水质成因研究:以山东省东营市孤岛镇为例 |
| 92 | 2058 | `1a1b1a47828c` | 地质学报 | 2025 | 金沙江上游色拉古滑坡复活特征与堵江溃决模拟分析 |
| 93 | 2059 | `693bbe5572de` | 地学前缘 | 2025 | 华北平原浅层地下水碘的分区特征及成因研究 |
| 94 | 2060 | `c977578193d3` | 矿床地质 | 2025 | 柴达木盆地狮子沟构造深层卤水水化学特征及成因研究 |
| 95 | 2061 | `d167056ccd2f` | 地质学报 | 2025 | 大洋多金属结核Ar- Ar测年方法——以克拉里昂- 克里帕顿断裂带为例 |
| 96 | 2062 | `c00d588b0d3b` | 地质学报 | 2025 | 藏东南然乌湖地区约33 ka以来古冰川作用的沉积响应 |
| 97 | 2063 | `be3d456e941a` | 地球科学 | 2026 | 藏东南缝合带蛇绿岩高温蚀变特性及其微观机制 |
| 98 | 2064 | `fb86044257e6` | 地球科学 | 2026 | 澜沧江中上游流域地貌特征及构造活动性评价方法 |
| 99 | 2065 | `cddf30e75c28` | 地质学报 | 2026 | 白云母和长石对稀有金属伟晶岩找矿方向的指示——以鲁中新泰市雁翎关锂辉石伟晶岩的发现为例 |
| 100 | 2066 | `7a3d8d0e41fc` | 地学前缘 | 2026 | 保定市区西部地下水循环规律及一亩泉成因研究:来自水化学、同位素和地下水位动态的证据 |
| 101 | 2067 | `aa7b820f97e8` | 地质学报 | 2026 | 华北晚石炭世大规模喀斯特型铝土矿成因研究进展 |
| 102 | 2068 | `1eecb4663a8d` | 地质学报 | 2026 | 湘东北稀有多金属矿集区深部电性结构及其地质意义 |
| 103 | 2069 | `bd11e6d9d690` | 地质学报 | 2026 | 高黎贡构造带中生代裂谷作用：来自滇西潞西碱性岩的证据 |
| 104 | 2070 | `27d9f1e058f2` | Mineralium Deposita | 2025 | Geology and geochronology of the Bangpu porphyry‒skarn‒epithermal Mo‒Cu‒Zn‒P… |
| 105 | 2071 | `363e34e64174` | Mineralium Deposita | 2022 | Biotite composition as a tracer of fluid evolution and mineralization center… |
| 106 | 2072 | `4797e31ab065` | Mineralium Deposita | 2024 | Magma mixing and magmatic-to-hydrothermal fluid evolution revealed by chemic… |
| 107 | 2073 | `7ccffef19042` | Mineralium Deposita | 2022 | Porphyry-epithermal Cu-Mo-Au–Ag mineralization in the Nakhodka ore field, Ba… |
| 108 | 2074 | `7a1f01a9fa6d` | Mineralium Deposita | 2026 | Assessing the geochemical anomalies of bismuth, gallium, indium, and telluri… |
| 109 | 2075 | `11a9997a56e6` | Mineralium Deposita | 2025 | Tracing fluid evolution forming multistage wolframite mineralization in vein… |
| 110 | 2076 | `2d7a2090a072` | Mineralium Deposita | 2022 | Constraints on ore-forming fluid evolution and guidance for ore exploration … |
| 111 | 2077 | `50bf80210d34` | Mineralium Deposita | 2023 | Evolution of the magmatic-hydrothermal system at the Erdenetiin Ovoo porphyr… |
| 112 | 2078 | `63fbe8467db7` | Mineralium Deposita | 2024 | Cassiterite geochronology and geochemistry of the Yunling Sn deposit: implic… |
| 113 | 2079 | `0bab391d7569` | Mineralium Deposita | 2023 | Lithium and oxygen isotopic constraints on the source and evolution of ore-f… |
| 114 | 2080 | `c20892ccbec8` | Mineralium Deposita | 2024 | Fluid evolution of the Lindero porphyry gold deposit, NW Argentina: the crit… |
| 115 | 2081 | `19cbb57d928e` | Mineralium Deposita | 2025 | Geochemical variability of tourmaline and chlorite in the Triangle deposit, … |
| 116 | 2082 | `2fcc9d632a8c` | Mineralium Deposita | 2024 | The Davenda-Klyuchevskoe Au-Mo-(Cu) cluster in the Mogocha gold district (Ru… |
| 117 | 2083 | `ec921f3587cb` | Mineralium Deposita | 2022 | Machine learning for geochemical exploration: classifying metallogenic ferti… |
| 118 | 2084 | `2e93c743e7eb` | Mineralium Deposita | 2025 | Apatite cathodoluminescence and trace element geochemistry of Neoarchean and… |
| 119 | 2085 | `5a2d42ba869f` | Mineralium Deposita | 2024 | The Jinchang deposit, Ailaoshan Belt: overprint of Miocene orogenic gold min… |
| 120 | 2086 | `120d103a231d` | Mineralium Deposita | 2024 | Geology and geochronology of the Banchang distal Cu-Mo skarn deposit, Centra… |
| 121 | 2087 | `9a5b8fd08087` | Mineralium Deposita | 2024 | Paleozoic orogenic gold mineralization from metamorphism of volcanic sequenc… |
| 122 | 2088 | `ace577db35b8` | Mineralium Deposita | 2025 | Linking two types of breccias to sediment-hosted stratiform copper (SSC) and… |
| 123 | 2089 | `0b3b9394de43` | Mineralium Deposita | 2026 | Copper and molybdenum isotope signatures along a vertical section through th… |
| 124 | 2090 | `195a8e7afe77` | Mineralium Deposita | 2022 | Genesis of hydrous-oxidized parental magmas for porphyry Cu (Mo, Au) deposit… |
| 125 | 2091 | `64bcb2271654` | Mineralium Deposita | 2023 | Fingerprinting alteration and mineralization in the iron oxide Cu-Au (IOCG) … |
| 126 | 2092 | `0950be37d305` | Mineralium Deposita | 2024 | Tracing the magmatic-hydrothermal evolution of the Xianghualing tin-polymeta… |
| 127 | 2093 | `c4e30cb76ddc` | Mineralium Deposita | 2024 | Hypogene sulfide precipitation during phyllic alteration: insights from copp… |
| 128 | 2094 | `3c3aa6a672e3` | Mineralium Deposita | 2025 | Trace element zoning and temperature variation in sphalerite from the giant … |
| 129 | 2095 | `0785962e4fdb` | Mineralium Deposita | 2025 | Geology and genesis of the Paodaoling intermediate-sulfidation epithermal go… |
| 130 | 2096 | `4cf57b22a4df` | Mineralium Deposita | 2022 | Karst-hosted Mississippi Valley-type Pb–Zn mineralization in fold-thrust sys… |
| 131 | 2097 | `96e62c257a12` | Mineralium Deposita | 2021 | Precise ages of gold mineralization and pre-gold hydrothermal activity in th… |
| 132 | 2098 | `45b6bf78adcd` | Mineralium Deposita | 2023 | Redox states and genesis of Cu- and Au-mineralized granite porphyries in the… |
| 133 | 2099 | `92cb09a03ffb` | Mineralium Deposita | 2024 | The Boring Billion: A key to resolving controversy on ore-fluid source model… |
| 134 | 2100 | `6083d5b95008` | Mineralium Deposita | 2024 | Hosts of Sn in reduced deep-seated W skarn systems: A case study on the worl… |
| 135 | 2101 | `77e038513bc0` | Mineralium Deposita | 2024 | Chalcopyrite, sphalerite, and pyrite chemistry in stratiform sediment-hosted… |
| 136 | 2102 | `74806a7bce96` | Mineralium Deposita | 2025 | U-Pb calcite geochronology indicates episodic amagmatic free-convection over… |
| 137 | 2103 | `da0d1bfe3aaf` | Mineralium Deposita | 2022 | U–Pb vein xenotime geochronology constraints on timing and longevity of orog… |
| 138 | 2104 | `f1cc5a24fa6c` | Mineralium Deposita | 2022 | Neoproterozoic tungsten mineralization: Geology, chronology, and genesis of … |
| 139 | 2105 | `10295d7a2977` | Mineralium Deposita | 2023 | Mineralogy, fluid inclusion and stable isotope study of the Jinchanghe Zn-Pb… |
| 140 | 2106 | `789e06cc8b2b` | Mineralium Deposita | 2023 | Hydrothermal monazite and xenotime chemistry as genetic discriminators for i… |
| 141 | 2107 | `b2801ca0ea1b` | Mineralium Deposita | 2026 | Geochemistry and Li isotopes of micas: implications for granitic magma evolu… |
| 142 | 2108 | `4411c2d89357` | Mineralium Deposita | 2023 | Mapping hydrothermal alteration zones with short wavelength infrared (SWIR) … |
| 143 | 2109 | `e02005cf0da6` | Mineralium Deposita | 2024 | In-situ geochronology combined with geochemical and isotopic signatures reco… |
| 144 | 2110 | `c16b3a46c63a` | Mineralium Deposita | 2026 | Magmatic–hydrothermal mineralization processes in the Xiwukou Sn–W–Rb deposi… |
| 145 | 2111 | `5a41e198b2c8` | Mineralium Deposita | 2022 | New insights about the origin of the Shilu Fe-Cu-Co deposit, Hainan Island, … |
| 146 | 2112 | `10c0ad0eeea0` | Mineralium Deposita | 2022 | Fluid evolution and ore genesis of the Tiantangshan granite-related vein-typ… |
| 147 | 2113 | `3295b4ce4676` | Mineralium Deposita | 2025 | The sulfur isotope evolution of the Duobuza Cu-Au porphyry deposit in the Du… |
| 148 | 2114 | `c03fb86e3a08` | Mineralium Deposita | 2022 | Lufilian copper–gold mineralization in the Mkushi District, Zambia: regional… |
| 149 | 2115 | `aa10b95fa654` | Mineralium Deposita | 2023 | Timing and origin of skarn-, greisen-, and vein-hosted tin mineralization at… |
| 150 | 2116 | `e326fc18632e` | Mineralium Deposita | 2024 | Relationship between U and Ni-Co-As mineralization in the Midwest polymetall… |
| 151 | 2117 | `446e59ed34e3` | Mineralium Deposita | 2025 | Controls of Early Jurassic Paleo-Pacific plate subduction on tectono-mineral… |
| 152 | 2118 | `674ff10a069f` | Mineralium Deposita | 2024 | Cassiterite and monazite U-Pb dating, and cassiterite geochemistry of the Sh… |
| 153 | 2119 | `73fc3f7c9053` | Mineralium Deposita | 2025 | Magmatic control on the formation of the Diaoquan Ag-(Cu-Au) skarn deposit i… |
| 154 | 2120 | `92bf85368d09` | Mineralium Deposita | 2022 | Timing and sources of skarn mineralization in the Canadian Tungsten Belt: re… |
| 155 | 2121 | `966c6f030137` | Mineralium Deposita | 2023 | Thermal history of Carlin-type gold deposits in Yukon (Canada) as revealed b… |
| 156 | 2122 | `c8740a0fea31` | Mineralium Deposita | 2024 | Using zircon and apatite chemistry to fingerprint porphyry Cu – Mo ± Au mine… |
| 157 | 2123 | `2e1bbe85cc90` | Mineralium Deposita | 2024 | Stable isotopes (H, C, O) and 3D fluid flow modeling constraints on gold end… |
| 158 | 2124 | `181aad0667b1` | Mineralium Deposita | 2025 | Tourmaline as a textural, geochemical and isotopic marker of fault valve pro… |
| 159 | 2125 | `2b15fc88ade5` | Mineralium Deposita | 2025 | Defining a Triassic carbonatite-related Mo-(REE) metallogenic belt along the… |
| 160 | 2126 | `6bd3036a4c6f` | Mineralium Deposita | 2025 | Genesis of the Zhaxikang Pb-Zn-Sb-Ag-Au deposit, southern Tibet: insights fr… |
| 161 | 2127 | `d175664810d1` | Mineralium Deposita | 2025 | Cassiterite U-Pb (ID-TIMS) age constraints on the timing of tin mineralizati… |
| 162 | 2128 | `5682017c6650` | Mineralium Deposita | 2026 | Structural controls of gold-bearing lodes at the Awanda orogenic gold deposi… |
| 163 | 2129 | `038bb19138fb` | Mineralium Deposita | 2022 | Genesis of the Hermyingyi W–Sn deposit, Southern Myanmar, SE Asia: Constrain… |
| 164 | 2130 | `471e15025631` | Mineralium Deposita | 2022 | Genesis of the Maogongdong deposit in the Dahutang W-Cu-(Mo) ore field of no… |
| 165 | 2131 | `3841ee5f0fbc` | Mineralium Deposita | 2022 | Bi/Te control on gold mineralizing processes in the North China Craton: Insi… |
| 166 | 2132 | `859b40624470` | Mineralium Deposita | 2023 | Hydrothermal apatite as a robust U–Th–Pb chronometer for the Carlin-type gol… |
| 167 | 2133 | `612ba6b45280` | Mineralium Deposita | 2023 | Metallogenic model of the Eocene Santa María and Antares Zn-Pb(-Ag) skarn de… |
| 168 | 2134 | `3877f7c595a8` | Mineralium Deposita | 2024 | Fluid-rock sulfidation reactions control Au-Ag-Te-Bi precipitation in the Va… |
| 169 | 2135 | `fdb7dca2841c` | Mineralium Deposita | 2022 | Decoupling of Sn and W mineralization in a highly fractionated reduced grani… |
| 170 | 2136 | `e6e37ee4b829` | Mineralium Deposita | 2022 | Scheelite chemistry from skarn systems: implications for ore-forming process… |
| 171 | 2137 | `653f81767ff2` | Mineralium Deposita | 2024 | Sulfide saturation in reduced magmas during generation of the Gangdese juven… |
| 172 | 2138 | `29bff332705d` | Mineralium Deposita | 2026 | Molybdenum isotope fractionation in Collision-type magmatic-hydrothermal sys… |
| 173 | 2139 | `976f83be8f52` | Mineralium Deposita | 2026 | Nodular pyrophyllite replacement of muscovite–quartz reflects paleo-isotherm… |
| 174 | 2140 | `2e75308dd7ac` | Mineralium Deposita | 2023 | Silver isotope analysis and systematics of native gold from the Rajapalot Co… |
| 175 | 2141 | `4874d2dc2ee9` | Mineralium Deposita | 2024 | Genesis of Pb–Zn-Ag-Sb mineralization in the Tethys Himalaya, China: Early m… |
| 176 | 2142 | `f11e1bee8231` | Mineralium Deposita | 2026 | Timing and evolution of REE–Fe–polymetallic skarn mineralisation in the REE-… |
| 177 | 2143 | `04b61d2855d3` | Mineralium Deposita | 2023 | Does SW China have Carlin-type gold deposits? A micro- to atomic-scale persp… |
| 178 | 2144 | `875dfb5c3ed9` | Mineralium Deposita | 2026 | Genesis of the Shiwu deposit in the Jinshan gold orefield: constraints from … |
| 179 | 2145 | `3280f64745fc` | Mineralium Deposita | 2026 | Fluid pathways, geochemical zonation, and exploration of Carlin-type gold de… |
| 180 | 2146 | `07285f809cc9` | Mineralium Deposita | 2023 | A regional petrogenetic–metallogenic perspective on the Kighal porphyry Cu d… |
| 181 | 2147 | `e6ac75d729ae` | Mineralium Deposita | 2023 | Zircon and the role of magmatic petrogenesis in the formation of felsic-host… |
| 182 | 2148 | `a416d295a0be` | Mineralium Deposita | 2024 | The magmatic origin of propylitic alteration of the Zhengguang epithermal Au… |
| 183 | 2149 | `8e9855e0aea8` | Mineralium Deposita | 2025 | Ore and gangue mineral textures, fluid inclusions, mesoscopically structured… |
| 184 | 2150 | `902b0fea84d5` | Mineralium Deposita | 2022 | Retraction Note to: Mercury isotope constraints on the sources of metals in … |
| 185 | 2151 | `286d2c534228` | Mineralium Deposita | 2023 | Origin and tectonic environment of the Longbohe Fe-Cu-(LREE) deposit in the … |
| 186 | 2152 | `b175094e23a2` | Mineralium Deposita | 2023 | Early zircon saturation in adakitic magmatic differentiation series and low … |
| 187 | 2153 | `a5c89f09f818` | Mineralium Deposita | 2024 | The In-Ga-Sb association of the post-Variscan Zn-Pb-Ag vein deposit at Laute… |
| 188 | 2154 | `4225911ecb8a` | Mineralium Deposita | 2025 | Deformation, alteration and implicit 3D geomodelling of deposit-scale contro… |
| 189 | 2155 | `e3d386ce8a92` | Mineralium Deposita | 2025 | Deciphering hydrothermal Te-Bi bonanza mineralization: In situ trace element… |
| 190 | 2156 | `537ee3ddaa0d` | Mineralium Deposita | 2025 | Formation of giant high-grade hypogene porphyry copper deposits during phyll… |
| 191 | 2157 | `011ec81b9b4c` | Mineralium Deposita | 2022 | A geochemical study of the Sweet Home mine, Colorado Mineral Belt, USA: form… |
| 192 | 2158 | `ab02a470529d` | Mineralium Deposita | 2024 | Sediment-derived granites as the precursor of rare-metal pegmatites in the P… |
| 193 | 2159 | `66a1499fdffc` | Mineralium Deposita | 2024 | Petrogenesis of Ke’eryin granitic pegmatites and associated Li mineralizatio… |
| 194 | 2160 | `57ed38e4a60e` | Mineralium Deposita | 2024 | Slab-derived fluids as a crucial factor for the metallogeny of porphyry depo… |
| 195 | 2161 | `b9fb81b88b52` | Mineralium Deposita | 2022 | Metal-rich organic matter and hot continental passive margin: drivers for De… |
| 196 | 2162 | `791f9f11f8b9` | Mineralium Deposita | 2022 | Pyrite trace element and S-Pb isotopic evidence for contrasting sources of m… |
| 197 | 2163 | `123b9044e073` | Mineralium Deposita | 2023 | The role of mantle melting in granite-associated hydrothermal systems: He–Ar… |
| 198 | 2164 | `e4f8344a275b` | Mineralium Deposita | 2025 | Large versus small Andean porphyry Cu deposits: insights from zircon petroch… |
| 199 | 2165 | `cd53d1c67883` | Mineralium Deposita | 2025 | Indium mineralization in the world-class Dachang Sn-polymetallic district, S… |
| 200 | 2166 | `b233c4ac2045` | Mineralium Deposita | 2025 | Coupling whole-rock geochemistry with trace elements and S-isotope analyses … |
| 201 | 2167 | `058025d28146` | Mineralium Deposita | 2026 | Lithium clay mineralization in the McDermitt volcano-sedimentary deposit, Or… |
| 202 | 2168 | `1d36148bc3c4` | Mineralium Deposita | 2022 | Skarn-style alteration in Proterozoic metasedimentary protoliths hosting IOC… |
| 203 | 2169 | `9f1643c0f05f` | Mineralium Deposita | 2022 | Texture and geochemistry of pyrite from the Jinya, Nakuang and Gaolong gold … |
| 204 | 2170 | `9a3dbfaa23ea` | Mineralium Deposita | 2023 | Breaking up the temporal link between granitic magmatism and iron oxide-copp… |
| 205 | 2171 | `a33d75c77ea7` | Mineralium Deposita | 2022 | Timing of magmatic-hydrothermal activity in the Variscan Orogenic Belt: LA-I… |
| 206 | 2172 | `b464fa8be3d2` | Mineralium Deposita | 2022 | Insight into the genesis of the Zhaosu Carboniferous Mn carbonate deposit (N… |
| 207 | 2173 | `ceee3b2075d1` | Mineralium Deposita | 2023 | Fluid inclusion and H–O-S-Pb isotope constraints on the origin of carbonate-… |
| 208 | 2174 | `49cec36e43bb` | Mineralium Deposita | 2023 | Contrasting platinum-group element geochemistry of post-collisional porphyry… |
| 209 | 2175 | `617286ac0462` | Mineralium Deposita | 2025 | Nonsulfide Zn–Pb mineralization and vertical zoning in the world-class Angou… |
| 210 | 2176 | `7d09808a7f4d` | Mineralium Deposita | 2026 | Extensive thallium mineralization resulting from diagenetic iron sulfide ads… |
| 211 | 2177 | `2f6d1172192f` | Mineralium Deposita | 2021 | The formation of (Ni-Co-Sb)-Ag-As ore shoots in hydrothermal galena-sphaleri… |
| 212 | 2178 | `ff72f11e538a` | Mineralium Deposita | 2022 | Superimposed microstructures of pyrite in auriferous quartz veins as fingerp… |
| 213 | 2179 | `776f78a39c15` | Mineralium Deposita | 2022 | Integrated stratigraphy, lithofacies, and U–Pb geochronology of the Myra Fal… |
| 214 | 2180 | `c969c0049709` | Mineralium Deposita | 2022 | Genesis of the exotic chrysocolla — “copper pitch/wad” — atacamite/brochanti… |
| 215 | 2181 | `70e492df36d2` | Mineralium Deposita | 2023 | Insights into the metamorphic history and origin of flake graphite mineraliz… |
| 216 | 2182 | `58733daa620a` | Mineralium Deposita | 2023 | Metal budget and origin of aqueous brines depositing deep-seated Zn-Pb miner… |
| 217 | 2183 | `fdefad30541e` | Mineralium Deposita | 2023 | Genesis of No. 2 orebody of the Jinchuan magmatic Ni-Cu-(PGE) sulfide deposi… |
| 218 | 2184 | `5000db6d12c0` | Mineralium Deposita | 2024 | Scheelite U–Pb dating constraints on the timing of the formation of the Zhaz… |
| 219 | 2185 | `049dbe0b3212` | Mineralium Deposita | 2024 | New insights on the formation of the polymetamorphic Felbertal tungsten depo… |
| 220 | 2186 | `99b0c189ab27` | Mineralium Deposita | 2024 | Coupled antimony and sulfur isotopic composition of stibnite as a window to … |
| 221 | 2187 | `ff06f9a7c4fb` | Mineralium Deposita | 2025 | Trace element distributions among Cu-(Fe)-sulfides from the Olympic Dam Cu-U… |
| 222 | 2188 | `673e9c91c8f2` | Mineralium Deposita | 2025 | Quartz and gold remobilization in orogenic Au deposits of the Val-d’Or vein … |
| 223 | 2189 | `f22723ce85fd` | Mineralium Deposita | 2026 | Seawater mixing as a mechanism to enrich gold ore: Insights from the Ludouso… |
| 224 | 2190 | `f679ff1cc53b` | Mineralium Deposita | 2026 | Unveiling the gold secrets of Jiaodong Peninsula: the role of felsic-mafic a… |
| 225 | 2191 | `7455d77f96ba` | Mineralium Deposita | 2022 | Intracrystalline migration of polymetallic Au-rich melts in multistage hydro… |
| 226 | 2192 | `eb17044f0500` | Mineralium Deposita | 2022 | Rhenium-osmium geochronology of gersdorffite and skutterudite-pararammelsber… |
| 227 | 2193 | `548225437700` | Mineralium Deposita | 2022 | Formation of Sn-rich granitic magma: a case study of the highly evolved Kafa… |
| 228 | 2194 | `f7cd22b49253` | Mineralium Deposita | 2022 | Exhalative red-bed copper mineralization in travertine, Puna Plateau, northw… |
| 229 | 2195 | `f41cb0ff3100` | Mineralium Deposita | 2022 | Orogenic gold: is a genetic association with magmatism realistic? |
| 230 | 2196 | `62f5d6b42b1e` | Mineralium Deposita | 2023 | Multi-stage metal enrichment and formation of gold mineralization in black s… |
| 231 | 2197 | `fe63e8fb150e` | Mineralium Deposita | 2024 | Mechanism of germanium enrichment in the world-class Huize MVT Pb–Zn deposit… |
| 232 | 2198 | `e641003f5134` | Mineralium Deposita | 2024 | Gold mineralization in the hydrothermal field at the termination of a detach… |
| 233 | 2199 | `b7549022632b` | Mineralium Deposita | 2024 | El Zorro: early Jurassic intrusion-related gold (IRG) mineralization in the … |
| 234 | 2200 | `6d04f123b193` | Mineralium Deposita | 2025 | Polymetallic vein formation through fluid flashing at the Sunnyside intermed… |
| 235 | 2201 | `0db0d4c5f0a2` | Mineralium Deposita | 2025 | The Karibib region as an emerging gold district in the Damara Belt, Namibia:… |
| 236 | 2202 | `d939255ed3b7` | Mineralium Deposita | 2022 | The Black Angel deposit, Greenland: a Paleoproterozoic evaporite-related Mis… |
| 237 | 2203 | `3472e423dc2d` | Mineralium Deposita | 2023 | Alpine eclogite-facies modification of Li-Cs-Ta pegmatite from the Wolfsberg… |
| 238 | 2204 | `e21d8e5678b2` | Mineralium Deposita | 2023 | Textural characterization of unidirectional solidification textures related … |
| 239 | 2205 | `f61eb34b3653` | Mineralium Deposita | 2023 | Molybdenum cycling in Andean-type subduction and metallogenic implications |
| 240 | 2206 | `1ac6e8795071` | Mineralium Deposita | 2024 | 2D and 3D textures of sulfide ores from the Shitoukengde Ni-Cu sulfide depos… |
| 241 | 2207 | `92989948d090` | Mineralium Deposita | 2024 | Age and genesis of hydrothermal Ni-PGE-Te mineralisation in the Gondpipri ma… |
| 242 | 2208 | `5150838a10bf` | Mineralium Deposita | 2026 | Au–Ag occurrence and Bi–Te redistribution in bornite-hosted exsolution micro… |
| 243 | 2209 | `c967f3191af0` | Mineralium Deposita | 2022 | Contributions of juvenile lower crust and mantle components to porphyry Cu d… |
| 244 | 2210 | `ca70d1534416` | Mineralium Deposita | 2024 | Polyphase stratabound scheelite-ferberite mineralization at Mallnock, Easter… |
| 245 | 2211 | `381fd943de19` | Mineralium Deposita | 2025 | Uranium-rich pyrochlore, thorite and associated minerals in the Muluozhai ra… |
| 246 | 2212 | `ef993fffe5e1` | Mineralium Deposita | 2025 | Constraints of in-situ elemental and B isotopic compositions of tourmaline o… |
| 247 | 2213 | `b185abcbcd6b` | Mineralium Deposita | 2025 | Structural controls on lithium mineralization in shear-zone hosted granitic … |
| 248 | 2214 | `5982fb3eab05` | Mineralium Deposita | 2022 | Trace-element distribution and ore-forming processes in Au–Ag-rich hydrother… |
| 249 | 2215 | `3fd01eac8979` | Mineralium Deposita | 2023 | Recognition of a ca. 130 Ma Makeng-Yangshan iron skarn belt in the Southeast… |
| 250 | 2216 | `dfa7fc31bb8f` | Mineralium Deposita | 2024 | Sodic-calcic alteration and transpressional shear along the Atacama fault sy… |
| 251 | 2217 | `a1da0ecf56c6` | Mineralium Deposita | 2024 | Mineralogy, mineral chemistry, and genesis of Cu-Ni-As-rich ores at Lisheen,… |
| 252 | 2218 | `69ec0b049b85` | Mineralium Deposita | 2025 | Cassiterite U–Pb ages from the Tin Islands, Indonesia, the southern end of t… |
| 253 | 2219 | `f73724535e31` | Mineralium Deposita | 2026 | An epigenetic model for the Yanjiao sandstone-hosted Zn–Pb deposit, China: c… |
| 254 | 2220 | `3b0b3194fca2` | Mineralium Deposita | 2022 | Variation of trace elements in chalcopyrite from worldwide Ni-Cu sulfide and… |
| 255 | 2221 | `6056b2efe479` | Mineralium Deposita | 2022 | Trace element distribution and geochemical zonation in the world-class Nowa … |
| 256 | 2222 | `e8539c02cc32` | Mineralium Deposita | 2022 | Zircon U–Pb geochronology of manganese-rich rocks from the Borborema Provinc… |
| 257 | 2223 | `45e05d7cf996` | Mineralium Deposita | 2022 | Petrogenesis of extra-large flake graphite at the Bissett Creek deposit, Can… |
| 258 | 2224 | `4c043670fdb5` | Mineralium Deposita | 2024 | Unraveling the evolution of the Bodó W-skarn district in the Seridó Mineral … |
| 259 | 2225 | `9fdd717e0dce` | Mineralium Deposita | 2025 | Genesis of the hot spring mercury deposits of the Mt. Amiata geothermal syst… |
| 260 | 2226 | `50b3e417851a` | Mineralium Deposita | 2025 | Discussion on “Granite-pegmatite-related gold skarns and associated Li-Cs-Ta… |
| 261 | 2227 | `0ffdec7f5361` | Mineralium Deposita | 2026 | Petrogenesis of chromitite ores in the Dongqiao ophiolite (Tibetan Plateau):… |
| 262 | 2228 | `a6238e359f87` | Mineralium Deposita | 2026 | Deciphering geochemical variations driven by metasomatism around a lithium p… |
| 263 | 2229 | `f9b45f660bef` | Mineralium Deposita | 2026 | Speculations on the genesis of enigmatic Pt-Ir-Fe-Ni micro-nuggets from the … |
| 264 | 2230 | `84d7c081dde3` | Mineralium Deposita | 2022 | Mechanism of organic matter assimilation and its role in sulfide saturation … |
| 265 | 2231 | `f6c7b6c0c04f` | Mineralium Deposita | 2022 | Geology of the Altamira and Las Luces deposits, Coastal Cordillera, northern… |
| 266 | 2232 | `d1c681a593c0` | Mineralium Deposita | 2022 | Vein-type gold formation during late extensional collapse of the Eastern Des… |
| 267 | 2233 | `28f18cae128f` | Mineralium Deposita | 2023 | Fluid partitioning between veins/fractures and the host rocks in Carlin-type… |
| 268 | 2234 | `a93a8e8f941a` | Mineralium Deposita | 2023 | Sulfide anatexis during high-grade metamorphism: a case study from the Boden… |
| 269 | 2235 | `2d265f8c708d` | Mineralium Deposita | 2023 | Redox-controlled olivine-sulfide equilibration of the Shitoukengde Ni–Cu dep… |
| 270 | 2236 | `4adcc3f665a1` | Mineralium Deposita | 2023 | The role of supermountain belts and climatic controls on the genesis of copp… |
| 271 | 2237 | `ddfa889a4d6e` | Mineralium Deposita | 2024 | Petrogenesis of magmatic REE mineral occurrences near Jamestown, Colorado (U… |
| 272 | 2238 | `6485ca4ebfdb` | Mineralium Deposita | 2024 | Petrological, geochemical and mineralogical characteristics of Wigu Hill car… |
| 273 | 2239 | `01c14ba609d2` | Mineralium Deposita | 2024 | Zircon petrochronology and chemistry reveal the formation of the giant tungs… |
| 274 | 2240 | `6b9e8edcaca1` | Mineralium Deposita | 2025 | Unravelling the mechanisms underlying marine redox shifts during sedimentary… |
| 275 | 2241 | `66e9b6567afa` | Mineralium Deposita | 2025 | Hydrothermal co-enrichment of Au, As, Co, and Cu in orogenic deposits: insig… |
| 276 | 2242 | `6105d55545ce` | Mineralium Deposita | 2025 | Two-stage melt extraction model for the Laiziling rare metal granites relate… |
| 277 | 2243 | `ed685dcd48b4` | Mineralium Deposita | 2026 | Magmatic evolution and mush processes of the Xiarihamu Ni-Cu deposit: insigh… |
| 278 | 2244 | `8fa8ff84fbf6` | Mineralium Deposita | 2023 | Mo endowment of porphyry deposits in the southern margin of the North China … |
| 279 | 2247 | `74d3770ebe5c` | Mineralium Deposita | 2024 | Role of chalcophile element fertility in the formation of the eastern Tethya… |
| 280 | 2250 | `1356b07db6fa` | Mineralium Deposita | 2025 | Cobalt-rich diagenetic Mn-oxide mineralization in the Neogene onshore Pisco … |
| 281 | 2251 | `aa5125ed9efe` | Mineralium Deposita | 2025 | Stable (Zn, Cd, Fe) and radiogenic (U, Pb) metal isotope constraints on the … |
| 282 | 2252 | `88c3f7ff1779` | Mineralium Deposita | 2026 | Microtextural characteristics of adularia in banded quartz veins from the Mi… |
| 283 | 2253 | `8f0a0ad38663` | Mineralium Deposita | 2026 | Introduction to the special issue on pegmatite-hosted Li-Cs-Ta-Sn deposits |
| 284 | 2254 | `6515aa33278f` | Mineralium Deposita | 2022 | Archetypal Au-bearing silica sinter from the Miocene Milestone deposit, Idah… |
| 285 | 2255 | `16a504aabb7a` | Mineralium Deposita | 2022 | Textural and compositional evolution of niobium minerals in the Miaoya carbo… |
| 286 | 2256 | `974e870363bb` | Mineralium Deposita | 2022 | The Miocene Western Balkan lithium-boron metallogenic zone |
| 287 | 2257 | `039f0b489d9d` | Mineralium Deposita | 2023 | Textural and compositional constraints on the origin, thermal history, and R… |
| 288 | 2258 | `5bc70066c724` | Mineralium Deposita | 2023 | Mississippi Valley-type Zn–Pb deposits in orogenic thrust belts: ore formati… |
| 289 | 2259 | `8300902560bf` | Mineralium Deposita | 2023 | Petrogenesis of Fe-Ti-P mineral deposits associated with Proterozoic anortho… |
| 290 | 2260 | `1fc59d5525d1` | Mineralium Deposita | 2024 | Origin of the giant Devonian Daxigou sedimentary siderite deposit, Central C… |

---

## P1（7 篇）— 有条目无知识点 —— 补知识点（论文数不变）

| 序 | 台账行 | sha12 | 期刊 | 年 | 标题 |
|---|---|---|---|---|---|
| 1 | 1341 | `9e0cb3778c76` | American Mineralogist | 2026 | RETRACTED: Shallow emplacement of silver-rich rhyolitic magma enhancing enri… |
| 2 | 1414 | `c4750718a05f` | Mineralium Deposita | 2025 | Granite-pegmatite-related gold skarns and associated Li-Cs-Ta pegmatites in … |
| 3 | 1418 | `0ca90eb3fa96` | Mineralium Deposita | 2023 | Petrogenesis of the Mesoarchaean Stella layered intrusion, South Africa: imp… |
| 4 | 1470 | `9976ce6286d6` | 地质学报 | 2026 | 江西省奉新县白果地区富锂花岗岩地球化学、年代学及矿物学特征 |
| 5 | 1472 | `b2845bacce16` | 地质学报 | 2026 | 喜马拉雅造山带铜矿勘查新进展：札达铜矿的发现与初步研究 |
| 6 | 1473 | `14da0a69fa2e` | 地质学报 | 2026 | 华北克拉通东南部本溪组金红石型钛成矿地质体物质来源及源汇控制模式 |
| 7 | 1474 | `b6ffcb415725` | 地质学报 | 2026 | 柴达木盆地昆特依盐湖石盐氯同位素地球化学特征及资源环境意义 |

---

## P2（16 篇）— 缺分面 —— 补受控词（论文数不变）

| 序 | 台账行 | sha12 | 期刊 | 年 | 标题 |
|---|---|---|---|---|---|
| 1 | 23 | `f9e86fc17b35` | FRONTENV | 2025 | 2025_FRONTENV_Ke_fenvs_656 |
| 2 | 27 | `a25354853db2` | FRONTENV | 2025 | 2025_FRONTENV_Pan_fenvs_698 |
| 3 | 37 | `7de66d5a5850` | FRONTENV | 2026 | 2026_FRONTENV_Swearer_fenvs_578 |
| 4 | 46 | `fd7f877f9e64` | FRONTENV | 2025 | 2025_FRONTENV_Lin_fenvs_437 |
| 5 | 51 | `2ee494864c0f` | FRONTENV | 2025 | 2025_FRONTENV_Wang_fenvs_297 |
| 6 | 52 | `e7c903f6a8c6` | FRONTENV | 2025 | 2025_FRONTENV_Yao_fenvs_528 |
| 7 | 59 | `a7aa8b3334e8` | FRONTENV | 2026 | 2026_FRONTENV_Li_fenvs_333 |
| 8 | 70 | `93c9c8672675` | FRONTENV | 2025 | 2025_FRONTENV_Chen_fenvs_214 |
| 9 | 740 | `6d5336ca4f90` | OTHERS | 2026 | 2026_OTHERS_Dong_s16_10305_y |
| 10 | 741 | `9dd952b57171` | OTHERS | 2026 | 2026_OTHERS_Enyi_s46_00058_6 |
| 11 | 742 | `5d8d82bef07c` | OTHERS | 2026 | 2026_OTHERS_Ritter_s16_00604_w |
| 12 | 745 | `1c89264610f6` | OTHERS | 2026 | 2026_OTHERS_Sereti_s06_04946_1 |
| 13 | 966 | `419437e95b7d` | 大地构造与成矿学 | 2026 | 《大地构造与成矿学》征稿简则 |
| 14 | 986 | `212b6bcfc7d2` | 地球科学 | 2026 | 场地孔隙含水层非均质性对污染物迁移转化与修复的影响 |
| 15 | 1031 | `6ab7d4f3b463` | Geology | 2026 | Squids radiated globally prior to the Cretaceous–Paleogene mass extinction |
| 16 | 1925 | `4883339873d2` | 地球科学 | 2026 | 热液成矿过程岩石差异破裂机制 |

---

## 执行约定

1. **从头开始，不跳序**：`prep` 已按 `ledger_idx` 升序取，直接 `prep --limit N --bucket P0` 即可。
2. **每批 5–10 篇**，处理完汇报；状态机幂等，可随时中断续跑。
3. **知识点 2–8 条不凑数**，按信息密度取最有价值的。
4. **窗口 23:00–次日 08:00**，窗口内零 DeepSeek 调用。
5. 每次开工先 `scan` 刷新队列 —— 新下载的会排到队尾，不影响已定顺序。