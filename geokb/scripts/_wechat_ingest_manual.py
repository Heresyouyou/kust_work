# -*- coding: utf-8 -*-
"""微信 2026-10 批次：人工逐篇分析，生成 31 个 papers 条目 + pdf_state 登记。

分析依据：每篇 PDF 首页题录 + 摘要 + 结论（state/wechat_202610_meta/slices/）。
extraction: manual（人工精读），confidence: B。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONTENT = BASE / "content" / "papers"
STATE_DIR = BASE / "state"

TODAY = "2026-10-16"
ORIGIN = "https://sjck.kust.work"

# sha1 与归档计划一一对应（跳过 [14] 重份）
E = [
 dict(sha="408d43b8441e377808ce97de972531043d03ca21", title="碰撞型斑岩系统铜钼成矿的岩浆挥发分控制（EG 2026）",
  journal="Economic Geology", year="2026", jdir="ECONGEO", date="2026-05-14", slug="Sun_Magmatic_Volatile_Cu_Mo_Collisional_Porphyry",
  tags=["斑岩铜矿","斑岩钼矿","岩浆挥发分","氧逸度","磷灰石地球化学","冈底斯","华北克拉通南缘","成矿理论"],
  summary="对比冈底斯铜相关与华北克拉通南缘钼相关斑岩岩浆的挥发分演化：二者均中等氧化（ΔFMQ>+1.5），铜岩浆初始 H2O 更高（5–6 wt%）且 F 强烈亏损形成富 Cl 残余熔体利于铜搬运，钼岩浆晚期 F 富集降低熔体粘度促进钼富集；提出 H2O-F-Cl 耦联演化是联系构带尺度构造与成矿类型的定量纽带。",
  key="针对碰撞型造山带斑岩系统中 Cu 与 Mo 成矿分异的控制因素这一问题，选取冈底斯带 Cu 相关岩浆与华北克拉通南缘 Mo 相关岩浆，以磷灰石地球化学结合热力学正演模拟定量重建岩浆 H2O-卤素演化轨迹。结果显示两系统氧化程度相近（ΔFMQ > +1.5），说明氧逸度是必要但不充分条件；关键差异在于饱和流体前的卤素轨迹——Cu 相关岩浆初始富 F（0.40–0.55 wt%）但随角闪石结晶显著亏损 F（bulk Dc/m F ≈ 1.8），残余熔体富 Cl、有利于 Cu 的络合搬运；Mo 相关岩浆初始 F 低（0.05–0.25 wt%），演化中因角闪石晶出有限而持续富集 F（Dc/m F ≈ 0.7），晚期 F 降低熔体粘度、延长分离结晶并促进 Mo 在残余熔体中富集。两类岩浆初始挥发分预算的差异最终源于不同源区与岩石圈结构：冈底斯 Cu 岩浆源自俯冲改造的富挥发分加厚镁铁质地壳，南缘 Mo 岩浆主要来自古老下地壳熔融。",
  methods=["磷灰石主量与挥发分（H2O、Cl、F、S）电子探针/LA-ICP-MS 分析","岩浆热力学正演模拟（rhyolite-MELTS 类）","H2O-Cl-F 质量平衡与分配系数计算"],
  area="西藏冈底斯带与华北克拉通南缘（碰撞型斑岩成矿带）",
  apps=["成矿理论","找矿预测（斑岩 Cu/Mo 含矿性判别）","教学参考"],
  title_en="Magmatic Volatile Controls on Cu and Mo Mineralization in Collisional Porphyry Systems"),
 dict(sha="1bd75d8824e6dfd38d662ed19879285d85b10ce2", title="东阿尔卑斯钨矿化：Felbertal 与区域白钨矿产出的矿物化学对比（EG 2026）",
  journal="Economic Geology", year="2026", jdir="ECONGEO", date="2026-01-01", slug="Altenberger_W_Eastern_Alps",
  tags=["钨矿","白钨矿","微量元素","矿床判别","变质热液","岩浆热液","奥地利","指示矿物"],
  summary="对东阿尔卑斯 Felbertal 世界级钨矿及 16 处白钨矿产出的系统对比，建立侵入岩相关型、多金属型、碳酸盐地层型三类矿化样式；提出 Sr/Mo-Na/Nb 双对数二元图解可可靠区分岩浆热液与变质热液白钨矿，抗后生改造且经全球数据集验证。",
  key="钨被列为美国与欧盟关键矿产，东阿尔卑斯是研究变质与岩浆热液钨矿化关系的天然实验室。本文将全球级 Felbertal 钨矿的白钨矿矿物化学与区域 16 处产出对比，厘定侵入岩相关、多金属、碳酸盐地层型三类矿化样式；指出 Na、Sr、Nb、Mo、REE+Y 是灵敏的成因指示元素，且 REE 配分在矿床内部甚至颗粒内部尺度均有显著变化，必须结合 CL 微区结构建立时间演化序列。核心成果是 Sr/Mo 对 Na/Nb 的 log-log 二元判别图解：无需多元统计、抗变质后生改造，可有效区分岩浆热液与变质（造山）成因白钨矿，并凸显 Felbertal 作为受变质改造的岩浆热液型矿床的独特地球化学属性；应用于全球数据集的检验证实了其普适性。",
  methods=["白钨矿阴极发光（CL）成像","LA-ICP-MS 原位微量元素","电子探针主量分析","区域对比与判别图解构建"],
  area="东阿尔卑斯（奥地利；Felbertal 钨矿及 Subpenninic–Austroalpine 构造单元）",
  apps=["矿床判别图解（指示矿物填图）","成矿理论","找矿预测"],
  title_en="Tungsten Mineralization in the Eastern Alps (Austria)"),
 dict(sha="c451270c83bc104d60f66ce2fb627f62c596f8ad", title="藏南错那洞 Sn-W-Be 多金属矿床成矿流体来源与演化（OGR 2022）",
  journal="Ore Geology Reviews", year="2022", jdir="OGR", date="2021-11-08", slug="Dai_Cuonadong_Sn_W_Be",
  tags=["矽卡岩","白钨矿","锡矿","铍","Sr同位素","REE地球化学","藏南","成矿流体"],
  summary="金云母 Ar-Ar 定年限定错那洞矽卡岩型矿化发生于约 15 Ma；白钨矿 REE 受 3Ca2+=2REE3++□Ca 替代机制控制、继承成矿流体特征；Eu 异常与 Sr 同位素指示岩浆热液流体在矽卡岩化与云英岩化过程中被强烈水岩反应改造。",
  key="错那洞是藏南特提斯喜马拉雅带内重要的 Sn-W-Be 多金属矽卡岩矿床，形成时代与成矿流体来源长期缺乏约束。本文以白钨矿微量元素与 Sr 同位素为核心证据，金云母 40Ar-39Ar 定年限定矽卡岩型矿化约 15 Ma；白钨矿 REE 以 3Ca2+ = 2REE3+ + □Ca 空位耦合替代进入晶格，直接继承成矿流体特征。两类白钨矿截然不同的 Eu 异常结合初始 87Sr/86Sr（介于错那洞淡色花岗岩与大理岩之间）证明：成矿流体为岩浆热液成因，但在矽卡岩化与云英岩化阶段经历了强烈的流体-岩石相互作用，化学与同位素组成被围岩显著改造。",
  methods=["金云母 40Ar-39Ar 定年","白钨矿 LA-ICP-MS 微量元素与 Sr 同位素","电子探针矿物化学","δEu-δCe 体系分析"],
  area="藏南错那洞（特提斯喜马拉雅构造带）",
  apps=["成矿流体示踪","区域成矿规律（喜马拉雅淡色花岗岩成矿系统）"],
  title_en="Source and evolution of the ore-forming fluid of the Cuonadong Sn-W-Be polymetallic deposit (southern Tibet, China): Constraints from scheelite trace element and Sr isotope geochemistry"),
 dict(sha="d09fc3f7c165c7adb05f2681ccf5d7651f992bbe", title="铜山口斑岩-矽卡岩 Cu-Mo(-W) 矿床多期热液白钨矿的结构与地球化学（AM 2020）",
  journal="American Mineralogist", year="2020", jdir="AM", date="2020-01-01", slug="Han_Tongshankou_Scheelite",
  tags=["斑岩铜矿","矽卡岩","白钨矿","阴极发光","Sr同位素","镁铁质包体","长江中下游","流体-岩石相互作用"],
  summary="铜山口三期白钨矿（深部浸染状、矽卡岩、远端石英脉）CL 与地球化学揭示：溶解-再沉淀可显著改造白钨矿成分；Sr 同位素证明深部镁铁质包体（W 含量 48–75 ppm）与花岗闪长斑岩出溶流体相互作用是钨萃取成矿的重要机制。",
  key="铜山口是长江中下游典型的斑岩-矽卡岩 Cu-Mo(-W) 矿床。本文按产状识别三期白钨矿：深部蚀变花岗闪长斑岩中的浸染状 Sch A、退化蚀变矽卡岩中 Sch B、穿切大理岩的远端石英脉 Sch C；CL 显示 Sch A 内部清晰（A-I）与浑浊多脉（A-II）两个亚型，后者由溶解-再沉淀形成、LREE/HREE 与 Sr 同位素（0.7080–0.7100，高于原生白钨矿 <0.7080）均明显不同。原生白钨矿 Sr 同位素与矿化花岗闪长斑岩（0.7061–0.7063）及镁铁质包体（0.7058–0.7073）一致；包体 W 含量（48–75 ppm）远高于斑岩（3–11 ppm），指示包体-岩浆流体相互作用对钨的萃取与成矿起重要作用。文章同时警示：使用白钨矿地球化学约束成矿流体前必须查明结构世代关系，否则成分可能被成矿期改造误导。",
  methods=["白钨矿 CL 成像与世代划分","LA-ICP-MS 微量元素","Sr 同位素（溶液法）","寄主岩石与包体全岩 W 含量对比"],
  area="湖北大冶铜山口（长江中下游成矿带）",
  apps=["成矿机制（斑岩-矽卡岩系统 W 来源）","方法参考（白钨矿结构-成分联合解析）"],
  title_en="Texture and geochemistry of multi-stage hydrothermal scheelite in the Tongshankou porphyry-skarn Cu-Mo(-W) deposit, eastern China: Implications for ore-forming process and fluid metasomatism"),
 dict(sha="190f21ff7003b39bd6a35caab6f95f5db76d043d", title="湘南韦家钨矿石榴子石-白钨矿化学对富 F 体系 W 矽卡岩成矿的约束（OGR 2022）",
  journal="Ore Geology Reviews", year="2022", jdir="OGR", date="2022-01-29", slug="Huang_Weijia_W_Skarn",
  tags=["矽卡岩","白钨矿","石榴子石","稀土元素","流体演化","富F体系","南岭","钨矿"],
  summary="韦家富 F 钨矿中识别出早期富 Cl 流体（致矽卡岩化）与晚期富 F 流体（致 W 矿化）两期出溶；交代作用由扩散型向对流型（W/R 升高）转变利于成矿；F 消耗降低流体 ΣREE，酸性含 W 流体经中和与围岩供 Ca 触发白钨矿沉淀。",
  key="韦家钨矿是湘南富 F 体系的 W 矽卡岩矿床，多矿物联合研究可提供成矿流体演化的完整画像。石榴子石与白钨矿微量元素表明：早期出溶富 Cl 岩浆流体（LREE 富集、HREE 亏损）驱动成矿前矽卡岩化；晚期富 F 流体（REE 配分平坦）负责 W 矿化。交代作用从扩散型向对流型转变、水岩比持续升高，为高流量含矿流体输送创造水动力条件；成矿流体中 F 的消耗降低了 ΣREE、Y、Nb、Ta 并增强 LREE/HREE 分馏，大气水混入使氧逸度升高，最终酸性含 W 流体的中和与围岩释放 Ca 触发白钨矿大规模沉淀。",
  methods=["石榴子石与白钨矿 LA-ICP-MS 微量元素剖面","电子探针矿物化学","REE 配分与流体演化反演","扩散-对流交代判别"],
  area="湖南韦家（湘南 W 多金属成矿区）",
  apps=["成矿流体演化重建","找矿预测（富 F 体系 W 矽卡岩）"],
  title_en="Garnet and scheelite chemistry of the Weijia tungsten deposit, South China: Implications for fluid evolution and W skarn mineralization in F-rich ore system"),
 dict(sha="8d2f6c7c9f544a4d501eb8dd2692c5e1b1fc24f9", title="伏家山 W 矽卡岩白钨矿成分指纹记录岩浆流体脉冲式流动（AM 2024）",
  journal="American Mineralogist", year="2024", jdir="AM", date="2024-04-01", slug="Ji_Fujiashan_Scheelite",
  tags=["矽卡岩","白钨矿","阴极发光","Sr-O同位素","岩浆流体","脉冲流动","长江中下游","钨矿"],
  summary="伏家山四阶段白钨矿 CL-微量元素-Sr-O 同位素联合研究显示：Mo 富（2.1 wt%）白钨矿形成于岩浆流体持续供给体系，Mo 贫（0.2 wt%）者形成于限流体系；化学与同位素系统变化反映岩浆流体脉冲式流动，无需多源流体或氧化还原剧变解释。",
  key="针对矽卡岩 W 矿床中白钨矿成分变化究竟反映多源流体还是单一流体脉冲这一问题，本文对伏家山四个热液阶段（进变质矽卡岩、退变质矽卡岩、石英-硫化物脉、晚期方解石交代）白钨矿开展 CL 成像、原位微量元素与 Sr-O 同位素分析。振荡环带 CL 结构指示原生生长，溶解-再沉淀与补丁结构见于退化与晚期阶段；Mo 含量是流体供给状态的指纹：Mo 富白钨矿（2.1 wt%，伴高 Nb-Ta、正 Eu 异常、高 δ18O 5.2–5.9‰、低 87Sr/86Sr）生长于岩浆流体持续供给体系，Mo 贫者（0.2 wt%）生长于流体受限、元素逐渐被抽空的体系。δ18O 与 87Sr/86Sr 的系统性漂移记录流体与围岩持续反应及矿物沉淀的累积效应——同一岩浆流体的脉冲式流动即可解释全部观测，无需引入不同流体或截然不同的氧化还原条件。",
  methods=["白钨矿 CL 成像与世代划分","LA-ICP-MS 微量元素","原位 Sr 同位素（fs-LA-MC-ICP-MS）","O 同位素（石英-白钨矿氧同位素测温）"],
  area="安徽伏家山（长江中下游成矿带）",
  apps=["成矿机制（流体供给方式）","方法参考（同位素示踪岩浆流体脉冲）"],
  title_en="Scheelite composition fingerprints pulsed flow of magmatic fluid in the Fujiashan W skarn deposit, eastern China"),
 dict(sha="3254b1b10f97b8d70a44299b60337e05d950e4b3", title="荞麦山 Cu-W 矽卡岩矿床石榴子石-白钨矿-磷灰石 U-Pb 联合定年（GSF 2023）",
  journal="Geoscience Frontiers", year="2023", jdir="FRONTGEO", date="2023-01-01", slug="Li_Qiaomaishan_U_Pb",
  tags=["矽卡岩","白钨矿U-Pb","石榴子石U-Pb","磷灰石U-Pb","成矿定年","长江中下游","多矿物联合定年"],
  summary="石榴子石、磷灰石、白钨矿三种矿物 LA-ICP-MS U-Pb 年龄在误差内一致（约 139 Ma），并与成矿相关花岗闪长斑岩锆石 U-Pb 年龄一致，精确限定荞麦山成矿时代；氧逸度下降使白钨矿富 U 是其可定年的前提，白钨矿 U-Pb 法快速经济、适用面广。",
  key="荞麦山 Cu-W 矽卡岩矿床的成矿时代此前缺乏直接约束。本文对三类矿石/脉石矿物（石榴子石、白钨矿、磷灰石）分别做原位 LA-ICP-MS U-Pb 定年，结果在误差内一致且与成矿相关花岗闪长斑岩的锆石年龄吻合，证明矿床形成于约 139 Ma，与岩体侵位直接相关。三期白钨矿记录了成矿体系氧逸度随时间下降的过程，而 fO2 降低正是白钨矿 U 含量升高、从而满足原位定年条件的控制因素。作者强调：白钨矿原位 U-Pb 相比 Sm-Nd 等效法更快速、经济、精确，但应用前需充分了解矿床、正确选择测年样品。",
  methods=["石榴子石/白钨矿/磷灰石 LA-ICP-MS 原位 U-Pb 定年","锆石 U-Pb 对比","白钨矿微量元素（U 富集机制与 fO2 关系）"],
  area="安徽荞麦山（长江中下游成矿带）",
  apps=["成矿定年方法","成矿时代约束（139 Ma 岩浆-成矿耦合）"],
  title_en="Combined garnet, scheelite and apatite U-PPb dating of mineralizing events in the Qiaomaishan Cu-W skarn deposit, eastern China"),
 dict(sha="418c5b2f6264a76bb5fd7f862dd25b7dab188918", title="都龙 Sn-Zn-In 多金属矿床石榴子石生长史：地球化学与 U-Pb 年龄约束（JGE 2025）",
  journal="Journal of Geochemical Exploration", year="2025", jdir="JGE", date="2025-01-28", slug="Liu_Dulong_Garnet",
  tags=["矽卡岩","石榴子石U-Pb","锡矿","锌","铟","滇东南","成矿定年"],
  summary="都龙矽卡岩识别两类石榴子石：Grt I（93.0–90.9 Ma，低水岩比、较还原）与 Grt II（80.4 Ma，高水岩比、较氧化）；核-幔-边成分环带记录水岩比先升后降及 pH-fO2 演化；石榴子石 U-Pb 直接约束 Sn-Zn 矽卡岩系统多期成矿。",
  key="都龙是滇东南超大型 Sn-Zn-In 矽卡岩-硫化物型多金属矿床，精细成矿年代学长期不足。本文识别两类石榴子石-辉石组合：Grt I 富 ΣREE、Y、HFSE、U，形成于低水岩比、相对还原环境，核（钙铝榴石）→幔（钙铁榴石）→边（固溶体）反映水岩比先升后降、pH 中低、fO2 先降后升；LA-ICP-MS U-Pb 获 Grt I 93.0–90.9 Ma、Grt II 约 80.4 Ma，分别与东部隐伏花岗岩、花岗斑岩的岩浆-热液活动对应，将矽卡岩形成分为两期。研究示范了石榴子石原位 U-Pb 对 Sn-Zn 矽卡岩系统成矿时代直接约束的突出价值。",
  methods=["石榴子石与辉石电子探针/LA-ICP-MS 成分剖面","石榴子石 LA-ICP-MS 原位 U-Pb 定年","核-幔-边温压-pH-fO2 反演"],
  area="云南马关都龙（滇东南老君山成矿区）",
  apps=["成矿定年（两期矽卡岩）","成矿流体演化","区域对比（与南洋田、老银山成矿时代对比）"],
  title_en="Growth history of garnet from the Dulong Sn-Zn-In polymetallic deposit: Geochemical and U-Pb age constraints and their metallogenic significance"),
 dict(sha="f2505abcab246f779b1d652d95540e47dfd4cf3a", title="白钨矿作为矿床类型判别矿物：微量元素与 REE 化学评估（CanMin 2018，经典判别图解）",
  journal="The Canadian Mineralogist", year="2018", jdir="CANMIN", date="2018-01-01", slug="Poulin_Scheelite_Discriminator",
  tags=["白钨矿","矿床判别","稀土元素","Sr-Mo-Eu图解","指示矿物","找矿预测","经典文献"],
  summary="系统考察全球 37 类矿床产状的白钨矿，首次建立 EuA 对 Sr/Mo 判别图解：还原型造山 Au-W 矿床白钨矿 As-Mo 亏损，氧化型侵入相关矿床 Mo 富集（>100 ppm）；REE 配分形态多变、δ18O 主要受流体混合控制；奠定白钨矿晶体化学作为成矿条件指示与矿床类型判别的基础。",
  key="这是白钨矿判别矿物学的奠基性工作。作者用 CL、SEM-EDS、LA-ICP-MS 与 δ18O 联合方法系统分析全球 37 类不同矿床环境（造山型、沉积/绿岩容矿、矽卡岩、斑岩、云英岩、VMS、角砾岩、多金属脉）的白钨矿，评估其晶体化学能否判别成因环境。要点：（1）白钨矿记录复杂生长历史，主替代为 As5+/Mo6+↔W6+、Sr2+/REE3+↔Ca2+；（2）Mo 与 As 为氧化还原敏感元素，还原造山型 Au-W 白钨矿 As、Mo 亏损，氧化侵入相关型 Mo 富集（>100 ppm）；（3）Eu 异常值与 Sr 在造山型相对富集；（4）据此建立首个白钨矿矿床判别图解 EuA vs Sr/Mo，对本研究样品判别成功；（5）REE 分馏程度与形态多变，受流体化学与共沉淀矿物等多因素控制，单独使用判别力有限；（6）δ18O 在 -4.6 至 +12.7‰ 大范围变化，主要反映流体混合而非矿床类型专属信号。",
  methods=["CL 成像与元素面扫描","LA-ICP-MS 微量元素","δ18O 同位素","判别图解统计构建"],
  area="全球 37 个矿床/产状（加拿大为主）",
  apps=["找矿预测（重砂白钨矿指示矿物填图）","矿床判别方法学","教学参考"],
  title_en="Assessing scheelite as an ore-deposit discriminator using its trace-element and REE chemistry"),
 dict(sha="73d79f996422606d8406228554b5c612e88408ff", title="Otago 片岩造山 W-Au 矿化中白钨矿与方解石原位 87Sr/86Sr 揭示近源与远源流体-岩石反应（EG 2018）",
  journal="Economic Geology", year="2018", jdir="ECONGEO", date="2018-01-01", slug="Scanlan_Otago_Sr_Scheelite",
  tags=["造山型金矿","白钨矿","Sr同位素","原位微区","流体-岩石相互作用","新西兰","成矿流体"],
  summary="新西兰 Otago 片岩两期造山 W-Au 矿化（约 135 Ma 与 105 Ma）白钨矿原位 Sr 同位素显示：成矿流体在至少 km 级（可能数十 km）尺度迁移，源自或强烈交换于更深部放射性成因的 Aspiring/Rakaia 杂岩；薄片尺度同位素不平衡与方解石继承白钨矿同位素组成，证明原位 Sr 法是强大的热液矿物世代示踪工具。",
  key="造山型 W-Au 系统中白钨矿与方解石均为低 Rb 矿物，适合原位 Sr 同位素分析以刻画流体-岩石反应程度。本文对 Otago 片岩两期矿化（第一期约 135 Ma、体积大、成矿深度大；第二期约 105 Ma）的白钨矿开展原位 87Sr/86Sr 分析：两期白钨矿 Sr 同位素相似，说明 Sr（及可能的 Au、W）源自类似储库；但 Caples 地体中第一期 Glenorchy 与第二期 Waipori 白钨矿普遍比其围岩更放射成因、更接近深部岩石，指示成矿流体在至少 km 级尺度运移，来源于或与更深部 Aspiring/Rakaia 杂岩强烈交换。薄片尺度同位素不平衡与方解石继承白钨矿同位素组成揭示了成矿后局部再活化。",
  methods=["白钨矿与方解石原位 87Sr/86Sr（LA-MC-ICP-MS）","两期矿化对比（135 Ma / 105 Ma）","薄片尺度同位素填图"],
  area="新西兰 Otago Schist（Glenorchy、Waipori 等矿区）",
  apps=["成矿流体示踪","方法参考（原位 Sr 同位素）","造山型 W-Au 成矿理论"],
  title_en="In Situ 87Sr/86Sr of Scheelite and Calcite Reveals Proximal and Distal Fluid-Rock Interaction During Orogenic W-Au Mineralization, Otago Schist, New Zealand"),
 dict(sha="927c414e8aae6798e2e09277a7ba74380fcab96c", title="造山型金矿中白钨矿微量元素组成：判别特征与指示矿物填图（Miner Deposita 2020）",
  journal="Mineralium Deposita", year="2020", jdir="MINDEP", date="2020-01-01", slug="Sciuba_Orogenic_Gold_Scheelite",
  tags=["造山型金矿","白钨矿","稀土元素","PLS-DA判别","指示矿物","找矿预测","变质相"],
  summary="全球 25 个造山型金矿白钨矿系统研究：识别五种 REE 型式；主岩变质相与岩性显著控制白钨矿成分（高变质相低 REE-Y-Sr、高 Mn-Nb-Ta-V）；Sr/Mo/Eu/As 与 PLS-DA 统计可有效区分造山型与其他矿床类型，为重砂指示矿物找金提供判别依据。",
  key="本文系统分析 25 个不同地质背景的造山型金矿白钨矿（EPMA + LA-ICP-MS），建立面向找金勘查的判别地球化学特征。造山型金矿白钨矿显示五种 REE 配分型式：钟形（正/负 Eu 异常）、平坦（正 Eu 异常）、少见 LREE 富集与 HREE 富集型；REE 型式主要反映含金流体来源与共沉淀矿物组合而非流体-矿物分馏。主岩变质相是强控制变量：绿片岩相-角闪岩相（中高变质相）容矿的白钨矿低 REE/Y/Sr、高 Mn/Nb/Ta/V，低于中绿片岩相者则相反；沉积岩容矿者高 Sr-Pb-U-Th、低 Na-REE-Y。PLS-DA 与元素比值图解表明白钨矿 Sr、Mo、Eu、As、Sr/Mo 组合可将造山型金矿与其他矿床类型区分（REE 型式单独判别力弱），可直接服务指示矿物填图。",
  methods=["EPMA 与 LA-ICP-MS 微量元素","PLS-DA 偏最小二乘判别分析","元素比值判别图解","区域矿床对比（25 矿床）"],
  area="全球 25 个造山型金矿（加拿大、新西兰、澳大利亚等）",
  apps=["找矿预测（指示矿物填图）","矿床判别统计方法"],
  title_en="Trace element composition of scheelite in orogenic gold deposits"),
 dict(sha="fae960ea0ac340c6e7c872a9fa78b30428f0bf62", title="内蒙古红花尔基白钨矿床 Sm-Nd 定年与 REE 组成（Lithos 2016）",
  journal="Lithos", year="2016", jdir="LITHOS", date="2016-09-01", slug="Guo_Honghuaerji_Sm_Nd",
  tags=["白钨矿Sm-Nd定年","钨矿","石英脉型","Sr-Nd-Pb同位素","蒙古-鄂霍茨克洋","大兴安岭","成矿流体"],
  summary="红花尔基白钨矿 Sm-Nd 等时线 178.4±2.9 Ma，与寄主二长花岗岩侵位年龄一致；正 εNd(t)（+1.46~+1.52）、δ34S 与 Pb 同位素指示幔源特征岩浆流体；MREE 富集、强负 Eu 异常表明白钨矿自还原流体中晶出；成矿与蒙古-鄂霍茨克洋闭合后碰撞加厚下地壳部分熔融有关。",
  key="红花尔基是内蒙古中-大兴安岭唯一的大型白钨矿-石英脉型矿床。本文对白钨矿开展 Sm-Nd 定年：7 件样品等时线年龄 178.4±2.9 Ma（MSWD=0.87），与成矿相关二长花岗岩锆石 U-Pb 年龄（179.4–178.6 Ma）一致，成矿与岩体侵位无显著时差，时空与成因上均与寄主岩体密切关联。白钨矿 ΣREE+Y 高达 3339–6321 ppm，球粒陨石标准化配分 MREE 富集、强负 Eu 异常（Eu/Eu*=0.09–0.23），指示 Eu 以 Eu2+ 为主、自相对还原流体结晶，REE3+ 以 Na 为主导耦合替代进入 Ca 位。正初始 Nd 同位素（+1.46~+1.52）、δ34S（+2.0~+3.8‰）与 Pb 同位素共同指示成矿流体具岩浆来源与幔源特征；区域地质综合认为红花尔基花岗岩源于蒙古-鄂霍茨克洋闭合后碰撞造山导致的加厚下地壳部分熔融。",
  methods=["白钨矿 Sm-Nd 同位素等时线定年","LA-ICP-MS 原位 REE","硫化物 S-Pb 同位素","锆石 U-Pb 对比"],
  area="内蒙古红花尔基（中-大兴安岭成矿带）",
  apps=["成矿定年","区域成矿规律（蒙古-鄂霍茨克洋构造）"],
  title_en="Sm-Nd dating and REE composition of scheelite for the Honghuaerji scheelite deposit, Inner Mongolia, Northeast China"),
 dict(sha="cf8369b641530c8d3d0035a066017c92e2e9f636", title="矽卡岩型 W-Mo 矿床白钨矿元素与同位素标志：以皖南池州为例（AM 2014）",
  journal="American Mineralogist", year="2014", jdir="AM", date="2014-01-01", slug="Song_Chizhou_Scheelite",
  tags=["矽卡岩","白钨矿","Sr-Nd同位素","Mo地球化学","池州","钨钼矿","成矿物质来源"],
  summary="皖南池州鸡头山、百丈岩矽卡岩 W-Mo 矿床白钨矿研究：矽卡岩型白钨矿以高 Mo 与 HREE 强亏损区别于脉型 Au-W 与斑岩 W-Mo；δEu-Mo 相关揭示氧化条件 Mo 进入白钨矿、还原条件 Mo 以辉钼矿沉淀；Sr-Nd 同位素指示成矿物质以壳源为主。",
  key="本文以安徽池州矶头山、百丈岩两个矽卡岩型 W-Mo 矿床为例，系统刻画矽卡岩型白钨矿区别于脉型 Au-W 与斑岩 W-Mo 矿床的两项独特地球化学特征：高 Mo 含量（MoO3 与 WO3 负相关）与 HREE 强亏损。早世代矽卡岩矿物（石榴子石、透辉石、角闪石）晶出造成 HREE 从流体中抽提，是白钨矿强烈 HREE 亏损与 LREE-HREE 解耦的主因；REE 以 3Ca2+=2REE3++□Ca 机制进入晶格。δEu 与 Mo 的复杂相关关系记录氧化还原条件：氧化条件下 Mo 随 W 进入白钨矿并在 δEu≈0.8–1 达峰值，还原条件下 Mo 含量下降并以辉钼矿形式沉淀。Sr-Nd 同位素方面，矽卡岩型白钨矿 (87Sr/86Sr)i 中等（0.708–0.715）、εNd(t) 低（-16~–9.1），指示成矿物质主要源自地壳。",
  methods=["白钨矿主量-微量元素（ICP-MS）","Sr-Nd 同位素","CL 与矿物世代鉴定","区域矿床对比"],
  area="安徽池州（长江中下游成矿带，江南隆起北缘）",
  apps=["矿床判别（矽卡岩型标志）","成矿物质来源示踪"],
  title_en="Scheelite elemental and isotopic signatures: Implications for the genesis of skarn-type W-Mo deposits in the Chizhou Area, Anhui Province, Eastern China"),
 dict(sha="471ba6bd5125f223a8f6f38d12eec45252d28daf", title="石门寺 W-Cu-Mo 多金属矿床白钨矿微量元素与 Sr-Nd 同位素（AM 2017）",
  journal="American Mineralogist", year="2017", jdir="AM", date="2017-01-01", slug="Sun_Shimensi_Scheelite",
  tags=["白钨矿","Sr-Nd同位素","钨矿","江南钨矿带","流体-岩石相互作用","九岭","成矿物质来源"],
  summary="世界级石门寺钨矿（76 万 t WO3）白钨矿研究显示：Eu 正负异常记录成矿过程——负异常继承自斑状花岗岩，正异常来自围岩斜长石破坏；异常高的初始 87Sr/86Sr（0.7230–0.7657）超出正常花岗岩范围，证明新元古代双桥山群浅变质岩通过强烈水岩反应为晚期成矿贡献了流体与金属。",
  key="石门寺是华南近年发现的世界级 W-Cu-Mo 多金属矿床（WO3 储量 76 万吨），其成矿物质来源存在争议。本文对白钨矿开展微量元素与 Nd-Sr 同位素分析：REE 受 3Ca2+=2REE3++□Ca 空位替代控制；球粒陨石标准化配分中部分样品负 Eu 异常、部分正异常，记录成矿过程——负异常继承自成矿斑状花岗岩，正异常则来自流体-岩石相互作用对围岩斜长石的破坏；单颗粒白钨矿 CL 颜色（红→蓝→黄）与 REE 含量升高相关。寄主于中新生代斑状花岗岩的负 Eu 异常白钨矿形成于原生流体，寄生于新元古代花岗岩的正异常白钨矿沉淀于演化流体。白钨矿 εNd(t) 低（-6.1~–8.1）与中新生代斑状花岗岩一致，但初始 87Sr/86Sr 异常高且变化大（0.7230–0.7657），显著超出花岗岩范围——指示成矿流体与金属不能仅归因于斑状花岗岩，局部新元古代双桥山群浅变质岩（高 Rb/Sr、放射性成因 Sr）在成矿晚期通过强烈流体-岩石反应贡献了成矿物质。",
  methods=["白钨矿微量元素与 REE（LA-ICP-MS/溶液法）","Sr-Nd 同位素","CL 颜色-成分关系","寄主岩石对比（斑状花岗岩 vs 双桥山群）"],
  area="江西石门寺（江南钨矿带，九岭地区）",
  apps=["成矿物质来源（壳源多储库混合）","区域成矿规律（江南钨矿带）"],
  title_en="Trace elements and Sr-Nd isotopes of scheelite: Implications for the W-Cu-Mo polymetallic mineralization of the Shimensi deposit, South China"),
 dict(sha="b0d04712945cb42af69db6c66c36d31ac4e16e39", title="杨家山石英脉型钨矿白钨矿 CL、原位 Sr-Nd 同位素与微量元素示踪动态热液过程（AM 2021）",
  journal="American Mineralogist", year="2021", jdir="AM", date="2021-12-01", slug="Li_Yangjiashan_Scheelite",
  tags=["石英脉型","白钨矿","原位Sr-Nd同位素","阴极发光","稀土元素","湘中","流体-岩石相互作用"],
  summary="杨家山两期五亚型白钨矿 CL+LA-ICP-MS 元素面扫描+fs-LA-MC-ICP-MS 原位同位素联合解析：还原流体（Mo<46 ppm）中四种 REE 型式与 δEu 0.2–20.7 的复杂 Eu 异常由 pH 局部波动控制；Sr-Nd 同位素指示岩浆热液与新元古代板岩混合源，Sch2→Sch5 的 87Sr/86Sr 递减记录水岩反应强度衰减。",
  key="结构复杂矿物是记录动态热液过程的关键载体。本文对湘中杨家山石英脉型 W 矿两期五个亚型白钨矿（Sch1–Sch5）开展 CL、LA-ICP-MS 元素面扫描与飞秒激光原位 Sr-Nd 同位素的系统联测：CL 揭示振荡环带、补丁与溶解-再沉淀结构；白钨矿自还原流体（Mo<46 ppm）沉淀、接近端元成分，Sr（36–1025 ppm）、Nd（8–351 ppm）、Na（7–300 ppm）变化大；四种球粒陨石标准化 REE 型式（LREE/MREE/HREE 富集与平坦）共存，δEu 在 0.2–20.7 间极端变化甚至同颗粒内并存——流体成分、三种耦合替代机制与原生-次生过程共同控制，局部 pH 波动是复杂 Eu 异常的主因。原位 Sr-Nd 同位素变化范围大（(87Sr/86Sr)i 0.71336–0.72617、εNd(t) -24.9~–7.7），指示岩浆热液流体与新元古代板岩的混合源；Sch2→Sch5 的 Sr 同位素递减记录流体-岩石反应强度随时间减弱。",
  methods=["CL 成像","LA-ICP-MS 元素面扫描","fs-LA-MC-ICP-MS 原位 Sr-Nd 同位素","替代机制与 pH 反演"],
  area="湖南杨家山（湘中石英脉型 W 矿）",
  apps=["成矿流体动态过程示踪","方法参考（多技术联测复杂结构矿物）"],
  title_en="Tracking dynamic hydrothermal processes: Textures, in-situ Sr-Nd isotopes, and trace-element analysis of scheelite from the Yangjiashan vein-type W deposit, South China"),
 dict(sha="d0f23c66a1bf9c9ba94424e280835ae85cebe986", title="南岭两期岩浆作用与钨成矿：以侏罗纪河路口矿床为证（AM 2021）",
  journal="American Mineralogist", year="2021", jdir="AM", date="2021-09-01", slug="Cao_Helukou_W",
  tags=["两期成矿","锆石U-Pb","辉钼矿Re-Os","白钨矿","南岭","河路口","姑婆山","流体-岩石相互作用"],
  summary="姑婆山矿田河路口矿床（33752 t WO3）锆石 U-Pb 与辉钼矿 Re-Os 双重证据揭示早侏罗（约 184/183.5 Ma）与中侏罗（约 163.8/163.4 Ma）两期岩浆-成矿事件；Sch I 高 87Sr/86Sr 与强负 Eu 异常指示早侏罗流体受水岩反应强烈改造，Sch II 组成与中侏罗姑婆山花岗岩一致，为南岭侏罗纪两阶段成岩成矿历史提供新认识。",
  key="河路口是南岭姑婆山矿田新开采的中型 W 矿床（储量 33752 t WO3），发育矽卡岩型与少量蚀变花岗岩型矿体。锆石 LA-ICP-MS U-Pb 定年：细粒黑云母花岗岩 184.0±3.6 Ma、白云母花岗岩 163.8±1.5 Ma、蚀变花岗岩型钨矿石 181.5±2.1 Ma；辉钼矿 Re-Os：蚀变花岗岩型 183.5±2.8 Ma、矽卡岩型 163.4±2.8 Ma——证明早侏罗与中侏罗两期独立成矿。微量元素显示两期白钨矿受不同耦合替代机制控制；高 Mo 低 Ce 表明均自氧化岩浆热液流体沉淀。Sch I（早侏罗）具强负 Eu 异常（均值 0.03）与高 87Sr/86Sr（0.70939–0.71932），指示早侏罗成矿流体被流体-岩石相互作用强烈改造；Sch II（中侏罗）的 Sr 同位素、Eu 异常与 Y/Ho 与姑婆山中侏罗花岗岩一致，继承自花岗岩岩浆热液体系。",
  methods=["锆石 LA-ICP-MS U-Pb 定年","辉钼矿 Re-Os 定年","白钨矿微量元素世代对比"],
  area="广西河路口（南岭姑婆山矿田）",
  apps=["区域成矿规律（南岭两期 W 成矿）","成矿时代精细约束"],
  title_en="Two-stage magmatism and tungsten mineralization in the Nanling Range, South China: Evidence from the Jurassic Helukou deposit"),
 dict(sha="e52a22bde7db2fc1479d863ea2604e94e28a3e06", title="矿床尺度围岩反应与氧化态对白钨矿微量元素-Sr-Nd-O 同位素的控制（EG 2026）",
  journal="Economic Geology", year="2026", jdir="ECONGEO", date="2026-02-07", slug="Wang_Scheelite_WallRock_Reaction",
  tags=["白钨矿","Sr-Nd-O同位素","流体-岩石相互作用","判别指标警示","造山型钨矿","新西兰"],
  summary="新西兰石英脉群白钨矿微量元素-原位 Sr-Nd 同位素-δ18O 综合研究显示：白钨矿与共生热液矿物 Sr 同位素均较近源岩浆岩更放射成因，氧同位素测温 170–280°C 并指示大气水-岩浆/变质混合；警示 Eu*、Sr/Mo、As 等常用判别指标受局部因素复杂控制，判别成因必须结合同位素约束。",
  key="两个曾被解释为岩浆成因的石英脉群白钨矿综合研究（微量元素+原位 Sr-Nd 同位素+δ18O）表明：白钨矿高度非均一的 REE 配分偏离围岩特征，白钨矿与共生热液相均具比近源岩浆岩更放射成因的 Sr 同位素组成；石英-白钨矿氧同位素测温限定脉体形成于中低温（约 170–280°C），计算指示大气水-岩浆或岩浆-变质混合流体参与成矿。作者据此强调：常用白钨矿微量元素代用指标（Eu*、Sr/Mo、As）用于划分矿物世代或矿床类型时必须谨慎——它们是多种局部因素的复杂组合，既不一定反映 W 源，也不一定反映矿床类型；稳健的解释必须将微量元素与同位素约束相结合，以解析成矿流体的来源、迁移路径与时序演化。",
  methods=["白钨矿原位 Sr-Nd 同位素（LA-MC-ICP-MS）","δ18O 氧同位素测温","微量元素代用指标检验"],
  area="新西兰南岛（石英脉型 W 矿）",
  apps=["方法警示（判别指标适用边界）","成矿流体来源综合解析"],
  title_en="Scheelite Trace Elements and Sr-Nd-O Isotopes Influenced by Deposit-Scale Wall-Rock Reaction and Oxidation State"),
 dict(sha="e885f5fb76aac457b07eaec0b24aacfd749faa11", title="寨上 Au-Sb-W 矿床白钨矿结构与地球化学记录成矿性质与时代（CMP 2024）",
  journal="Contributions to Mineralogy and Petrology", year="2024", jdir="CMP", date="2024-01-01", slug="Weng_Zhaishang_Scheelite",
  tags=["卡林型金矿","白钨矿U-Pb","西秦岭","Au-W共生","稀土元素","Sr同位素","流体-岩石相互作用"],
  summary="西秦岭寨上卡林-like 金矿两类白钨矿原位 U-Pb 年龄 227.1±3.2 Ma 与 226.2±6.9 Ma，限定 Au-W 成矿与西秦岭印支期岩浆-构造事件同步；Sch A（低 REE、负 Eu）到 Sch B（高 REE、正 Ce、共生硒化物）记录流体自还原向氧化演化；Sr 同位素指示成矿物质以泥盆系地层淋滤为主、少量晚期岩浆混入，提出流体-岩石反应致 Fe-Ca 释放、共沉淀金与白钨矿的机制。",
  key="寨上是西秦岭巨型卡林-like 金矿（127 t Au @ 2.67 g/t，伴生 W、Sb）。本文按岩相学与地球化学识别振荡环带暗灰 CL 的 Sch A 与补丁状亮 CL 的 Sch B，LA-ICP-MS U-Pb 分别获 227.1±3.2 Ma 与 226.2±6.9 Ma，将 Au-W 成矿精确锚定于印支期，与西秦岭区域岩浆-成矿-构造事件同步，示范了白钨矿 U-Pb 对卡林/卡林-like 矿床直接定年的可靠性。Sch A 低 REE（均值 41.3 ppm）、负 Eu 异常；Sch B 高 REE（均值 247 ppm）、小负/正 Eu 异常与更高正 Ce 异常（共生硒化物、硫酸盐指示更高 fO2），Eu-Eu* 正相关记录整个 W 成矿期处于氧化条件。Sr 同位素指示成矿物质主要来自泥盆系地层淋滤，Sch B 中岩浆 Sr 比例增加；流体自更还原（Sch A）向更氧化（Sch B）演化对应水岩反应减弱。作者提出：贫 Fe-Mn-Ca-W 的母流体通过流体-岩石反应获取 Fe、Ca，在中低温体系中与 pH 升高共同导致载金黄铁矿、白钨矿与碳酸盐共沉淀。",
  methods=["白钨矿 LA-ICP-MS 原位 U-Pb 定年","CL 成像与世代划分","微量元素与 Sr 同位素","氧逸度指示矿物（硒化物/硫酸盐）"],
  area="甘肃寨上（西秦岭）",
  apps=["成矿定年（卡林-like Au-W）","Au-W 共生机制"],
  title_en="Scheelite texture and geochemistry as a recorder of nature and timing of metallogenesis: an example from the Zhaishang Au-Sb-W deposit, western Qinling, central China"),
 dict(sha="bc9fe6f0c593a1214621c53e18b1ad0cfebea9f4", title="滇东南南洋田钨矿石榴子石-符山石-白钨矿原位 U-Pb 定年揭示早白垩 W 成矿事件（Gondwana Research 2024）",
  journal="Gondwana Research", year="2024", jdir="GR", date="2024-01-01", slug="Zhao_Nanyangtian_Grt_U_Pb",
  tags=["矽卡岩","石榴子石U-Pb","符山石","白钨矿U-Pb","滇东南","老君山","叠加成矿","成矿定年"],
  summary="南洋田钨矿多矿物原位 U-Pb 揭示两期矽卡岩化：约 203 Ma（印支期变质事件）与约 141–146 Ma（W 成矿）；早白垩 W 成矿早于邻近老银山岩体与都龙 Sn-Zn 矿床，关联另一未揭露的岩浆热液系统；提出印支期预富集+早白垩深部流体活化的叠加成矿模式，并厘定老君山地区四期岩浆-成矿事件（445–410/215–203/152–140/100–75 Ma）。",
  key="南洋田是滇东南老君山地区重要 W 矽卡岩矿床，成矿时代与多期叠加关系不明。石榴子石、符山石、白钨矿原位 U-Pb 显示两期矽卡岩化：约 203 Ma 与 W 成矿期约 141–146 Ma；后者明显老于邻近老银山岩体和都龙 Sn-Zn 矿床，可能关联另一个尚未揭露的岩浆热液系统，并与古太平洋板块早白垩俯冲相关。作者提出叠加成矿模式：印支期区域构造-变质产生顺层/穿层裂隙等成矿空间并实现 W 的地层预富集，早白垩深部 W 富流体活化先存成矿元素，在矽卡岩、片岩与石英脉中沉淀多类型白钨矿。区域综述识别老君山地区四期岩浆-成矿事件：445–410 Ma、215–203 Ma、152–140 Ma 与 100–75 Ma。",
  methods=["石榴子石/符山石/白钨矿 LA-ICP-MS 原位 U-Pb 定年","岩相学与世代划分","区域成矿年代学综合"],
  area="滇东南南洋田（老君山 W-Sn 多金属矿集区，薄竹山—老君山地区）",
  apps=["区域成矿规律（滇东南多期叠加）","成矿定年","找矿预测（印支期预富集层位）"],
  title_en="In situ U-Pb dating of garnet, vesuvianite, and scheelite from the Nanyangtian tungsten deposit reveals an Early Cretaceous W mineralization event in Southeast Yunnan, China"),
 dict(sha="fac8e730e159dfa60cdb163508fa2fb23c98bf75", title="大兴安岭南段毡铺地区中生代两期岩浆成矿潜力评估：锆石年代学、微量元素与 Hf 同位素证据（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="RenQinghui_Zhanpu_Zircon",
  tags=["大兴安岭","锆石U-Pb","Hf同位素","氧逸度","岩浆水含量","锡成矿潜力","找矿预测"],
  summary="毡铺地区花岗岩（247±2 Ma）与闪长玢岩（147±1 Ma）两期岩浆 εHf(t) 均为正、指示新生地壳或幔源贡献；锆石微量元素显示两期均低氧逸度（ΔFMQ 均值 -2.71 与 -0.10），不具备斑岩 Mo-Cu 成矿的高氧逸度条件；早白垩世闪长玢岩适度富水，其高分异演化体系具备 Sn 多金属成矿潜力。",
  key="对大兴安岭南段毡铺地区新识别的花岗岩与闪长玢岩开展锆石 U-Pb-Hf 同位素与微量元素研究：两期岩浆年龄 247±2 Ma（早三叠世）与 147±1 Ma（早白垩世），与南段晚二叠-早三叠、晚侏罗-早白垩两期区域岩浆-成矿事件一致。εHf(t)（+6.5~+11.0 / +4.3~+8.7）与二阶段模式年龄（860–572 Ma / 925–644 Ma）指示显著新生地壳或幔源物质贡献。锆石氧逸度显示早三叠世花岗岩低氧逸度且贫水，不利于斑岩 Mo-Cu 成矿；早白垩闪长玢岩低氧逸度、适度富水，由其衍生的高分异岩浆有利于 Sn 等不相容元素富集——区内找矿应聚焦早白垩世还原性高分异花岗岩及其岩浆-热液系统。",
  methods=["锆石 LA-ICP-MS U-Pb 定年","Hf 同位素","锆石微量元素氧逸度计与岩浆水含量反演"],
  area="内蒙古毡铺（大兴安岭南段）",
  apps=["找矿预测（Sn 多金属靶区方向）","区域岩浆-成矿演化"],
  title_en="Evaluation of mineralization potential of two Mesozoic magmatic events in the Zhanpu area, southern Great Xing'an Range: Evidence from zircon geochronology, trace elements and Hf isotopes"),
 dict(sha="d638237107d982f0b78ce97105f9fc9ddd5e8f14", title="江南钨矿带桂林郑矿床富钼白钨矿稀土元素富集机制及其地质意义（矿床地质 2022）",
  journal="矿床地质", year="2022", jdir="矿床地质", date="2022-08-01", slug="RenKangda_Guilinzheng_REE",
  tags=["白钨矿","稀土元素","富钼白钨矿","江南钨矿带","LA-ICP-MS面扫描","综合利用"],
  summary="桂林郑富钼白钨矿 ΣREE 达 28.59–4863.82 ppm（均值 789.21）且逐期升高，REE 以 Ca2++Mo6+=REE3++(1-x)Mo5++xNb5+ 方式进入晶格；成矿流体 Cl 络合物降低、F 络合物增高与氧逸度下降是富钼白钨矿 REE 富集的有利条件；带内富钼白钨矿普遍比贫钼者更富 REE，具综合利用前景。",
  key="桂林郑 Mo-W 矿床位于江南钨矿带北段，矿石矿物为富钼白钨矿，稀土富集机制不明。对三个世代富钼白钨矿开展原位 LA-ICP-MS 微量元素与面扫描：ΣREE 从 Sch-I（均值 203.19 ppm）经 Sch-II（928.79）到 Sch-III（1547.13）逐期升高，LREE/HREE 相应降低；替代机制以 Ca-Mo→REE+Mo/Nb 耦合为主。富钼白钨矿形成于高氧逸度、富 F 岩浆热液体系，演化过程中流体 Cl 络合物降低、F 络合物增高、氧逸度下降共同促进 REE 富集。与江南带代表性矿床系统对比表明：富钼白钨矿 REE 显著高于贫钼白钨矿（如石门寺均值 553 ppm 对比多数贫钼矿床 <300 ppm），桂林郑矿石的稀土资源具综合利用前景。",
  methods=["富钼白钨矿 LA-ICP-MS 原位微量元素与元素面扫描","世代对比与替代机制反演","区域矿床统计对比"],
  area="江南钨矿带桂林郑矿床",
  apps=["稀土综合利用评价","白钨矿微量元素机理"],
  title_en="REE enrichment mechanism of high-Mo scheelite in the Guilinzheng Mo-W deposit, Jiangnan tungsten belt, and its geological significance"),
 dict(sha="2d2c3d4e225fca8d37c47dfbc043088cd0b87312", title="大兴安岭南段边家大院斑岩-隐爆角砾岩型 Sn 多金属矿床 Sn-Ag 赋存状态、富集机制及成矿模型（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="LiuJun_Bianjiadayuan_SnAg",
  tags=["隐爆角砾岩型","锡矿","银","赋存状态","成矿模型","大兴安岭","找矿预测"],
  summary="边家大院隐爆角砾岩型 Sn 矿化分脉型与筒型，具裂隙相-震裂相-震碎相-爆破相四相分带，Sn-Pb-Zn-Ag 矿化集中于震裂相与震碎相；锡以锡石为主，银矿物多达九种赋存于胶结物与石英-多金属硫化物脉；建立深部斑岩型 Sn-Cu-Mo-上部隐爆角砾岩型-远端脉状 Pb-Zn-Ag 的成矿模型，指出西部深部隐伏花岗岩与外围中低温 Ag-Pb-Zn 的找矿方向。",
  key="隐爆角砾岩型锡矿可独立成矿或作为 Ag-Pb-Zn 伴生矿大规模产出，但长期缺失普适成矿模型。边家大院是大兴安岭南段代表性隐爆角砾岩型 Sn 多金属矿床：钻探编录与岩相学确认隐爆角砾岩型为最重要 Sn 矿化类型，脉型与筒型两类，从浅到深、从中心到边部划分为裂隙相、震裂相、震碎相、爆破相四相带，Sn-Pb-Zn-Ag 矿化主要分布于震裂相与震碎相。锡矿物以锡石为主、少量黄锡矿；独立银矿物含螺状硫银矿、锑银矿、银黝铜矿、辉锑银矿、硫铁银矿、硫锑铜银矿、深红银矿与自然银，赋存于胶结物或石英-多金属硫化物脉。以隐爆角砾岩筒为中心，西部发育多个 Sn-Cu-Mo 中高温矿化中心（深部隐伏花岗岩体找矿重点），东部为大规模 Ag-Pb-Zn 中低温成矿区，外围西、南、北部具远端脉状 Pb-Zn-Ag 潜力。",
  methods=["钻探工程地质编录","显微岩相学与矿物鉴定","分相带空间结构解析","区域物性-化探综合分析"],
  area="内蒙古边家大院（大兴安岭南段）",
  apps=["找矿预测（隐爆角砾岩型 Sn 与外围 Ag-Pb-Zn 靶区）","成矿模型"],
  title_en="Occurrence, enrichment mechanism and metallogenic model of Sn-Ag in the Bianjiadayuan porphyry-cryptoexplosive breccia type Sn polymetallic deposit, southern Great Xing'an Range"),
 dict(sha="5e0d9494e33fa80636c469ced6c14a51891171bc", title="大兴安岭地区新生下地壳再造与大规模锡多金属成矿（岩石学报 2026，综述）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="Zhou_Mao_XinXiadike_Sn",
  tags=["大兴安岭","锡矿","新生下地壳","岩浆分异","挥发分","成矿模型","综述","找矿预测"],
  summary="大兴安岭晚中生代（150–130 Ma）大规模 Sn 多金属矿化常与 Ag-Pb-Zn 共伴生，传统上地壳变泥质岩源区模型无法解释；本文论证成矿岩体主要源自新生下地壳部分熔融，软流圈上涌热驱动岩浆高分异是 Sn 成矿必备条件，中-大型锡矿岩浆体系为水饱和富 F-B 花岗质熔体，火山通道相与环状断裂系统是值得关注的找矿新方向。",
  key="综述近年大兴安岭锡矿研究，回答三个关键问题：（1）源区——成矿岩体主要源自新生下地壳物质部分熔融，或少量古老陆壳混染，二叠系富锡地层与锡林郭勒杂岩可能提供 Sn 预富集，但岩浆高度分异是成矿的必备条件；（2）驱动机制——晚中生代古太平洋板块低角度俯冲与回撤造成岩石圈伸展，软流圈物质上涌的热驱动含锡岩浆分异，含矿流体对成矿元素的有效萃取受岩体浅侵位制约；（3）挥发分——中-大型锡矿岩浆体系为水饱和、富挥发分（F、B 等）花岗质熔体体系，大规模流体出溶、降温与水-岩反应是成矿关键。提出成矿构造包括断裂系统与褶皱，成矿结构面为硅钙面与岩体接触带；地球物理参数（重、电、磁）可有效定位深部隐伏锡矿体；建议加强火山-侵入杂岩成因、火山通道相与环状断裂系统研究。",
  methods=["区域矿床研究综述","岩浆源区与过程综合分析","成矿模型归纳"],
  area="大兴安岭成矿带（重点南段）",
  apps=["区域成矿规律","找矿预测（火山机构与深部隐伏矿体）","成矿理论"],
  title_en="Recycling of juvenile lower crust and large-scale tin polymetallic metallogenesis in the Great Xing'an Range"),
 dict(sha="bf2ecd63419a641f2a0bc2a687791bf022e3b4d5", title="滇东南官房钨矿床石榴子石原位 LA-SF-ICP-MS U-Pb 定年及地质意义（岩石学报 2021）",
  journal="岩石学报", year="2021", jdir="岩石学报", date="2021-03-01", slug="LiuYi_Guanfang_Grt_UPb",
  tags=["矽卡岩","石榴子石U-Pb","滇东南","薄竹山","钨矿","成矿定年","昆理工"],
  summary="官房矽卡岩 W 矿床石榴子石原位 LA-SF-ICP-MS U-Pb 获 101.3±5.4 Ma 与 87.6±2.3 Ma 两期年龄，指示早白垩与晚白垩两期矽卡岩事件；约 88 Ma 为该区 W-Sn 多金属主成矿期并与同期花岗岩密切相关，约 101 Ma 或代表另一期矽卡岩成岩事件，对滇东南找矿部署有实践意义。",
  key="官房是滇东南薄竹山 W 多金属矿集区的矽卡岩型钨矿床，白钨矿浸染状赋存于石榴子石-辉石矽卡岩中，矽卡岩多呈脉状/透镜状产于围岩、不直接与岩体接触。电子探针显示石榴子石为钙铁榴石-钙铝榴石固溶体系列，指示弱氧化性流体；原位 LA-SF-ICP-MS U-Pb 定年获 T-W 图下交点年龄 101.3±5.4 Ma（MSWD=2.0）与 87.6±2.3 Ma（MSWD=1.5），证明矿区存在早白垩与晚白垩两期矽卡岩事件。结合区域燕山晚期成岩成矿谱系：约 88 Ma 是滇东南 W-Sn 多金属主成矿期，与同期花岗岩密切相关；约 101 Ma 可能代表另一期矽卡岩成岩事件。研究同时示范了石榴子石原位 U-Pb 定年限定矽卡岩型 W 矿成矿时代的可行性。",
  methods=["石榴子石电子探针成分分析","石榴子石原位 LA-SF-ICP-MS U-Pb 定年","区域成矿时代谱系对比"],
  area="滇东南官房（薄竹山 W 多金属矿集区）",
  apps=["成矿定年","区域找矿部署（两期矽卡岩事件）"],
  title_en="In-situ LA-SF-ICP-MS U-Pb dating of garnet from the Guanfang tungsten deposit, Southeast Yunnan, and its geological significance"),
 dict(sha="cc160a6132c07efc0b3fcbb5649c35a061f9e236", title="大兴安岭南段地壳深熔阶段锡的迁移与富集机制（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="ZhuangSitong_CrustalMelting_Sn",
  tags=["锡矿","部分熔融","相平衡模拟","锡林郭勒杂岩","源区","大兴安岭","微量元素模拟"],
  summary="锡林郭勒杂岩黑云斜长片麻岩（黑云母 Sn 含量显著高于角闪岩相矿物）是更可能的成矿花岗岩源区；相平衡模拟显示部分熔融（850–910°C / 0.6–0.8 GPa）中黑云母完全分解时熔体 Sn 达峰值、富集约 2.4 倍；源区整体贫锡、无显著预富集，初步 Sn 富集发生于深熔阶段并受黑云母分解控制，压力升高利于富集而水含量增加抑制富集。",
  key="回答大兴安岭南段 Sn 成矿是否存在深部预富集源区及部分熔融阶段 Sn 迁移机理。全岩-矿物地球化学显示：黑云斜长片麻岩 Sn 含量高于角闪岩但仍低于上地壳均值，源区无显著预富集；黑云母（4.51 ppm）是源区主要载锡矿物，显著高于角闪岩中绿泥石（0.40–0.57）与角闪石（0.58–0.88），故黑云斜长片麻岩更可能为成矿花岗岩主源区。以黑云斜长片麻岩成分为基准的相平衡模拟表明：熔体 Sn 浓度随温度升高递增，黑云母完全分解时达峰值后被稀释；压力升高利于 Sn 富集、水含量增加抑制富集。微量元素模拟定量给出区内低分异锡成矿花岗岩形成条件 850–910°C / 0.6–0.8 GPa、部分熔融程度 19.47–32.94%，对应熔体 Sn 含量 4.84–5.51 ppm，与实测低分异花岗岩吻合——深熔阶段可实现约 2.4 倍初步富集，但不足以单独成矿。",
  methods=["全岩与矿物（黑云母/角闪石/绿泥石）原位微区 Sn 分析","相平衡模拟（Theriak-Domino 类）","微量元素部分熔融模拟"],
  area="大兴安岭南段（锡林郭勒杂岩分布区）",
  apps=["成矿源区理论（Sn 预富集评估）","区域成矿模型约束"],
  title_en="Transport and enrichment mechanism of tin during crustal anatexis in the southern Great Xing'an Range"),
 dict(sha="b91ba79b82549cdaed95837931cb2cda8f2df210", title="大兴安岭南段敖脑达坝斑岩型铜锡矿岩浆演化与锡成矿作用（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="ZhangChenming_Aonaodaba_CuSn",
  tags=["斑岩型","铜锡矿","A型花岗岩","岩浆水含量","氧逸度","锆石微量元素","大兴安岭"],
  summary="敖脑达坝含矿岩体形成于 152.8–149.3 Ma，为高钾钙碱性还原性 A 型花岗岩，源于新生下地壳重熔；含矿石英斑岩岩浆 H2O 高达 7.72–13.14%（深部富水岩浆注入所致）而氧逸度并未系统性升高——这种'富水而仍保持还原'的性质恰是 Sn 高效成矿的有利条件；高度分异与亚固相线流体对富锡围岩的萃取是形成大型锡矿的重要因素。",
  key="敖脑达坝是大兴安岭南段代表性斑岩 Sn-Cu 矿。锆石 U-Pb：新鲜石英斑岩 152.8±0.5 Ma、矿化蚀变石英斑岩 149.3±1.3 Ma、深部花岗斑岩 149.7±1.3 Ma，形成于晚侏罗世古太平洋低角度俯冲背景。全岩高硅富钾，属高钾钙碱性还原性花岗岩系列；锆石微量元素指示 A 型花岗岩、陆壳环境、新生下地壳部分熔融来源，并记录强烈的长石、榍石、磷灰石分离结晶。关键发现：含矿石英斑岩岩浆 H2O（7.72–13.14%，均值 10.40%）显著高于新鲜石英斑岩（4.89%）与花岗斑岩（7.15%），可能源于深部富铜富水岩浆注入；而三类岩石 ΔFMQ 基本一致（均值 +0.48~+0.93），说明 H2O 与氧逸度是相互独立的强度变量，'富水'不必然'氧化'——还原条件下 Sn 以 Sn2+ 不相容行为在残余熔体与出溶流体中高效富集迁移。区域对比进一步表明：岩浆高度分异叠加出溶流体在亚固相线条件下对富锡围岩的萃取与水-岩反应，可能是形成大型锡矿床的共同要素。",
  methods=["锆石 LA-ICP-MS U-Pb 定年","全岩主微量元素","锆石原位微量元素（氧逸度计、岩浆水含量反演）"],
  area="内蒙古敖脑达坝（大兴安岭南段）",
  apps=["成矿机制（富水还原岩浆 Sn 成矿）","找矿预测（斑岩型 Cu-Sn 判别）"],
  title_en="Magmatic evolution and tin mineralization of the Aonaodaba porphyry Cu-Sn deposit, southern Great Xing'an Range"),
 dict(sha="0a5801d9810712443af5da030284dc836c44af76", title="大兴安岭中南段锡多金属矿岩石物性特征及其找矿指示意义（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="ZhangMinghang_RockPhysics",
  tags=["岩石物性","锡矿","地球物理勘探","激发极化法","大兴安岭","找矿预测","浅覆盖区"],
  summary="系统测定大兴安岭中南段 521 件岩矿石样品密度、磁性、电阻率与极化率：金属矿石低阻高极化高密度，含锡花岗岩高阻低磁低密，锡石-硫化物型呈'低阻高极化高密度中等磁性'三高一中组合，锡石-石英脉型极高阻较高极化；据此提出分矿化类型的激电、重磁、电测深方法组合优选方案，服务覆盖区深部找矿。",
  key="针对大兴安岭中南段浅覆盖区隐伏矿体探测困难、已有物性工作零散的问题，系统采集 521 件岩矿石样品测定关键物性参数。电阻率跨四个数量级（52.95–16715.45 Ω·m），极化率 2%–75%，密度 2.52–4.65 g/cm3，磁性从抗磁到强磁均有。总体规律：金属矿石低阻、高极化、高密度；中酸性岩高阻、弱磁、低密；基性岩强磁。分矿化类型差异显著：锡石-硫化物型'低阻（高导）、高极化、高密度、中等磁性'（三高一中）；含锡花岗岩'高阻、低磁、低密'（指示还原环境贫磁铁矿的高分异岩浆本质）；锡石-石英脉型'极高阻、较高极化、中等密度、弱/无磁'。蚀变响应明确：硅化致电阻率跃升（>5000 Ω·m），黄铁矿化致极化率显著升高，磁黄铁矿化激发'高极化+高磁化'双高异常。据此提出：锡石-硫化物矿体圈定以激电法为主配合重力；锡石-石英脉型突出电测深+磁法联合抗干扰；含锡花岗岩以电测深圈定高阻岩体、磁法判别岩性。",
  methods=["系统岩石物性测量（密度/磁化率/电阻率/极化率，521 件）","分矿化类型物性组合统计","蚀变-物性响应分析"],
  area="大兴安岭中南段锡多金属成矿带",
  apps=["找矿预测（物探方法组合优选与联合反演）"],
  title_en="Rock physical properties of Sn polymetallic deposits in the central-southern Great Xing'an Range and their prospecting implications"),
 dict(sha="6cabf8683a598a7de06b9dbedd59bd95acb185bd", title="绢云母矿物指针指示内蒙古小乌兰锡铁矿床成矿流体运移过程（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="PengYao_Xiaowulan_Muscovite",
  tags=["绢云母","短波红外SWIR","锡矿","锡石U-Pb","成矿流体","蚀变矿物勘查","大兴安岭","找矿预测"],
  summary="小乌兰（宝盖沟）锡矿锡石 U-Pb 年龄 143.2±0.8 Ma，为大兴安岭南段燕山期大规模 Sn 多金属成矿事件组成部分；绢云母 SWIR Pos2200 与主微量元素空间分带指示热液活动中心位于西半区中部、流体沿北西-南东向断裂迁移；示范了'SWIR+矿物化学+定年'蚀变矿物指针在找矿靶区圈定中的应用。",
  key="小乌兰（宝盖沟）锡铁矿是大兴安岭南段腹地小型锡矿，采用蚀变矿物勘查方法寻求突破。锡石 LA-ICP-MS U-Pb 定年 143.2±0.8 Ma，属燕山期大规模 Sn 成矿事件。围岩蚀变以硅化、白云母化、绢云母化、绿泥石化为主（少量电气石化、黄玉化），整体带状分布、分带不明显，矿化与云英岩化密切相关。绢云母 Pos2200 整体偏低（2198–2209 nm，受酸性花岗质围岩控制）；空间上西半区中部高（>2205 nm，流体 pH 略偏中性、更靠近高温热液中心）并向南东、北西降低（2203–2206 nm，紧邻云英岩化矿脉、流体偏酸性）——该光谱特征是本区找矿标志。矿物化学上，西半区中部绢云母富集 Fe-Sn-Li-Rb，向外更富流体活动性元素 Nb-W-Cu-Pb-B-Ga，指示热液中心位置与流体沿 NW-SE 向构造迁移路径。SWIR 光谱与矿物化学相互印证，可有效示踪流体演化与迁移路径。",
  methods=["绢云母短波红外光谱（SWIR）","电子探针主量与 LA-ICP-MS 微量元素","锡石原位 U-Pb 定年"],
  area="内蒙古小乌兰/宝盖沟（大兴安岭南段）",
  apps=["找矿预测（蚀变矿物指针与靶区圈定）","成矿流体示踪"],
  title_en="Muscovite as a mineral vector tracing ore-forming fluid migration in the Xiaowulan (Baogaigou) Sn-Fe deposit, Inner Mongolia"),
 dict(sha="16a848192ab049ea9d7738bf38b7b2622a6722c9", title="赣中成矿带下桐岭特大型钨钼矿床黑钨矿 U-Pb 定年与化学成分研究（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="YangShiwen_Xiatongling_Wolframite",
  tags=["黑钨矿U-Pb","钨矿","石英脉型","赣中成矿带","燕山期","S型花岗岩","成矿定年"],
  summary="下桐岭石英脉型 W-Mo 矿床两类黑钨矿 U-Pb 年龄 160.5±3.4 Ma（板状 Wol-1）与 146.7±6.6 Ma（长针状 Wol-2），均形成于晚侏罗世并与辉钼矿同属一期成矿；黑钨矿富锰（FeO/MnO<1）指示弱酸性相对还原流体；赣中钨矿与还原高分异 S 型花岗岩、钨铜矿与相对氧化 I 型花岗岩有成因对应关系。",
  key="下桐岭是赣中成矿带特大型石英脉型钨钼矿床，产于燕山期中细粒花岗岩接触带。矿相学识别自形板状 Wol-1 与自形-半自形长针状 Wol-2 两类型，穿插关系证明 Wol-2 晚于 Wol-1。LA-ICP-MS U-Pb 定年：Wol-1 为 160.5±3.4 Ma、Wol-2 为 146.7±6.6 Ma，证实石英脉型黑钨矿形成于晚侏罗世，黑钨矿与辉钼矿同属一期成矿、与矿区晚侏罗世黑云母花岗岩成因相关。EPMA 显示两类黑钨矿 FeO/MnO 均小于 1（富锰），指示弱酸性成矿流体；Wol-1 落于黑钨矿端元区间、形成于相对还原高温环境，Wol-2 富 Mn 贫 Fe 暗示流体氧逸度略升——可能与少量大气降水混入有关。区域规律：赣中成矿带钨多金属成岩成矿集中于 160–150 Ma（燕山早期华南大规模岩浆活动产物）；钨矿与还原性高分异 S 型花岗岩有关，钨铜矿与相对氧化性 I 型花岗岩有关，找矿应重视高分异还原性 S 型花岗岩内外接触带及顶部。",
  methods=["黑钨矿 LA-ICP-MS 原位 U-Pb 定年","电子探针成分分析","矿相学与世代划分"],
  area="赣中成矿带下桐岭矿床（江西）",
  apps=["成矿定年","找矿预测（S 型花岗岩接触带）"],
  title_en="Wolframite U-Pb dating and chemical composition of the Xiatongling giant W-Mo deposit in the central Jiangxi metallogenic belt and their geological significance"),
 dict(sha="4c3afaf53ce0e3c14c9d059510c0397b70243291", title="大兴安岭南段白音查干超大型锡矿化富集机制：来自硫化物微区组成的制约（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="ChenXinkai_BayinChagan_Sn",
  tags=["超大型锡矿","闪锌矿","LA-ICP-MS微区","硫同位素","成矿流体演化","银矿","大兴安岭"],
  summary="白音查干超大型 Sn-Ag 矿床四阶段流体演化：Fe-Cd-In 在岩浆-热液过渡期达峰后下降，Sn-Ag-Sb-Pb 在晚期硫化物阶段富集；PCA 温度计显示流体自约 370°C 快速冷却至约 190°C 并有外部低温流体参与；δ34S 自早期均值 -5.1‰ 演至晚期 -15.1‰ 的极度负偏，指示晚期富氧大气水混入、减压沸腾驱动 Sn 络合物失稳沉淀、Ag-硫氢络合物解体引发银大规模卸载。",
  key="白音查干是大兴安岭南段最具代表性的超大型 Sn-Ag 多金属矿床之一。按矿化蚀变与脉体穿插关系将成矿流体演化划分为岩浆晚期、岩浆-热液过渡、热液成矿（分锡石-石英、锡石-硫化物高温阶段与低温硫化物阶段）与成矿期后四阶段。闪锌矿原位微区分析显示：Fe、Cd、In 在岩浆-热液过渡期达峰值后持续下降，Sn、Ag、Sb、Pb 在晚期硫化物阶段急剧富集；成矿元素经复杂耦合类质同象进入闪锌矿（如 2(Sb3+,As3+)+Pb2++□=4Zn2+），当流体浓度突破晶格容纳极限时辉银矿、黝铜矿等独立含银矿物开始沉淀。闪锌矿微量元素 PCA 温度计计算显示流体自中高温（约 370°C）向晚期低温（约 190°C）快速冷却，随机森林模型验证晚期有外部低温流体显著参与。δ34S 自早期相对集中（均值约 -5.1‰）向晚期极度负偏（最低 -15.1‰）且分布局限扩大，指示 SO42-/H2S 急剧升高，受控于富氧大气降水混入、快速冷却与减压沸腾去气。综合机制：流体-围岩中和反应与减压沸腾致锡络合物失稳驱动 Sn 大规模沉淀，晚期快速冷却与富氧大气水混入引发 Ag 的大规模卸载。",
  methods=["闪锌矿 LA-ICP-MS 原位微区成分","硫化物硫同位素","微量元素 PCA 温度计与随机森林验证","成矿阶段划分"],
  area="内蒙古白音查干（大兴安岭南段）",
  apps=["成矿机制（Sn-Ag 富集与沉淀触发）","找矿预测（硫同位素与矿物化学向量）"],
  title_en="Enrichment mechanism of the Bayinchagan superlarge tin mineralization in the southern Great Xing'an Range: Constraints from in-situ sulfide compositions"),
 dict(sha="b6e3cc7db8201f74606cae2a05103e12f634db3e", title="华北克拉通北缘各少沟地区二叠纪钨锡成矿事件：锡石、黑钨矿、独居石 U-Pb 年代学约束（岩石学报 2026）",
  journal="岩石学报", year="2026", jdir="岩石学报", date="2026-10-01", slug="WeiJialin_Geshaogou_W_Sn",
  tags=["二叠纪成矿","锡石U-Pb","黑钨矿U-Pb","独居石U-Pb","华北克拉通北缘","渣尔泰山","钨锡矿床","找矿预测"],
  summary="各少沟 W-Sn 矿床浅部锡石-黑钨矿-孔雀石石英脉锡石 U-Pb 279.8–276.5 Ma，深部浸染状矿化花岗岩黑钨矿 274.7–273.2 Ma、锡石 276.2–262.6 Ma、独居石 270.1–269.9 Ma，构建二叠纪多矿物成矿年代学格架；成矿与古亚洲洋闭合后碰撞伸展吻合，并与邻区甲生盘 277 Ma 花岗岩构成'同空间、异类型、同时代'矿床组合，指示华北克拉通北缘上千千米二叠纪花岗岩带具钨锡成矿条件。",
  key="各少沟是华北克拉通北缘渣尔泰山裂谷带新识别的与二叠纪岩浆热液活动相关的 W-Sn 多金属矿床。三种矿石矿物原位 U-Pb 联合定年：浅部含锡石-黑钨矿-孔雀石石英脉 3 件锡石 279.8–276.5 Ma；深部浸染状矿化花岗岩中黑钨矿 274.7–273.2 Ma、锡石 276.2–262.6 Ma、独居石 270.1–269.9 Ma——精细构建二叠纪成矿时间序列。成矿时代与古亚洲洋闭合后碰撞伸展阶段吻合，暗示动力学关联；邻区甲生盘大型 Pb-Zn 矿床深部已揭露约 277 Ma 二叠纪花岗岩，侵位时代与各少沟成矿高度一致，可能属同一岩浆系统，共同构成渣尔泰山裂谷带'同空间、异类型、同时代岩浆关联'矿床组合。从各少沟经甲生盘到华北克拉通北缘东西向绵延上千千米的二叠纪花岗岩带，本次成矿事件厘定指示该巨型花岗岩带具备 W-Sn 成矿地质条件，对区域找矿方向拓展具重要指导意义。",
  methods=["锡石/黑钨矿/独居石 LA-ICP-MS 原位 U-Pb 联合定年","多矿物年代学格架构建","区域岩浆-成矿事件对比"],
  area="内蒙古各少沟（华北克拉通北缘渣尔泰山裂谷带）",
  apps=["区域成矿规律（二叠纪花岗岩带 W-Sn 潜力）","找矿预测","成矿定年方法（多矿物联合）"],
  title_en="Permian W-Sn mineralization event in the Geshaogou area, northern margin of the North China Craton: Constraints from cassiterite, wolframite and monazite U-Pb geochronology"),
]


def yaml_str(s):
    return json.dumps(str(s), ensure_ascii=False)


def sentence_safe(text, limit=200):
    s = re.sub(r"\s{2,}", " ", (text or "").replace("\n", " ")).strip()
    if len(s) <= limit:
        return s
    w = s[:limit]
    pos = max((w.rfind(c) for c in "。！？；!?;"), default=-1)
    if pos >= limit * 0.4:
        return w[:pos + 1]
    pos2 = max((w.rfind(c) for c in "，,、"), default=-1)
    if pos2 >= limit * 0.4:
        return w[:pos2] + "……"
    return w + "……"


def md_list(items):
    return "\n".join(f"- {x}" for x in items) if items else "- （未能提取）"


CONTENT.mkdir(parents=True, exist_ok=True)
written = []
for e in E:
    eid = f"paper-{e['sha'][:12]}"
    rel = f"papers_repo/by_journal/{e['jdir']}/{e['date'][:4]}/{e['date']}_{e['slug']}.pdf"
    src_obj = {"name": e["journal"], "path": rel,
               "url": f"{ORIGIN}/api/entry/{eid}/pdf", "accessed": TODAY}
    tags_js = json.dumps(e["tags"], ensure_ascii=False)
    summary = sentence_safe(e["summary"])
    body = f"""## 关键内容

{e['key']}

## 核心方法

{md_list(e['methods'])}

## 研究区域

{e['area']}

## 应用场景

{md_list(e['apps'])}

## 来源

- 期刊：{e['journal']}（{e['year']}）
- 英文标题：{e['title_en']}
- 仓库文件：`{rel}`
- PDF 下载：https://sjck.kust.work/api/entry/{eid}/pdf
"""
    front = f"""---
id: {eid}
title: {yaml_str(e['title'])}
module: papers
tags: {tags_js}
summary: {yaml_str(summary)}
confidence: B
status: active
updated: {TODAY}
sources: [{json.dumps(src_obj, ensure_ascii=False)}]
journal: {yaml_str(e['journal'])}
year: {yaml_str(e['year'])}
extraction: manual
---

{body}"""
    (CONTENT / f"{eid}.md").write_text(front, encoding="utf-8")
    written.append(eid)
    print(f"wrote {eid}  {e['title'][:40]}")

# pdf_state.jsonl 登记：防止未来管线以 LLM 提取覆盖人工条目
state_file = STATE_DIR / "pdf_state.jsonl"
now = datetime.now().isoformat(timespec="seconds")
with open(state_file, "a", encoding="utf-8") as f:
    for e, eid in zip(E, written):
        rec = {"hash": e["sha"], "path": f"S:/GeoKB/papers_repo/by_journal/{e['jdir']}/{e['date'][:4]}/{e['date']}_{e['slug']}.pdf",
               "ts": now, "status": "done", "entry": eid, "note": "manual-ingest-2026-10"}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(f"\npdf_state.jsonl 登记 {len(written)} 条（status=done，管线将跳过）")
