# -*- coding: utf-8 -*-
"""把关注列表重新归类。

信号强弱排序：
  1. 这个 UP 最近一条投稿的分区（由 enrich.py 抓 tid、rank.py 的 tid->分区名 反查）
     ——他自己选的分区，比任何关键词都准
  2. 账号名含明显身份自述（"字幕组""讲史""考研"）
  3. 已入库视频标题的关键词
  4. 个性签名 —— 噪音最大，必须先剥掉商务联系方式再用

输出 data/cats.json。
"""
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ---------------------------------------------------------------- 目标分类
CATS = [
    ("news", "时政·社会"),
    ("biz", "经济·商业"),
    ("history", "历史"),
    ("ideas", "思想·人文"),
    ("sci", "科学·技术"),
    ("books", "读书·文学"),
    ("film", "影像·音乐"),
    ("game", "游戏"),
    ("study", "学习·课程"),
    ("life", "生活"),
    ("friend", "同学·熟人"),
    ("unknown", "待定"),
]
CAT_IDS = [c[0] for c in CATS]
CAT_NAME = {c[0]: c[1] for c in CATS}

# ---------------------------------------------------------------- 二级分类
# 拆的是两个最大的类。生活原来 121 人、影像·音乐原来 98 人，都是"什么都有"的
# 大筐——一个标签页里同时排着做饭和键政、或者同时排着翻唱和剧集解说。
# 每支都用一句话能说清收的是谁，说不清的就不该建这一支。
#
# 判据优先级即书写顺序：写在前面的词更专指，先中先得。
# 比如「厨房」只有做饭的人会用，排在「日常」前面；
# 「书房连着厨房」这种名字会先落到 food，那是他真正的内容。
SUBCATS = {
    "life": [
        ("food",   "吃·厨房",   "做饭、菜谱、探店、美食节目"),
        ("craft",  "手作·维修", "手工、DIY、修复、积木、居家妙招"),
        ("visual", "摄影·绘画", "拍照、风光、绘画、地图与地理影像"),
        ("style",  "穿戴·身型", "穿搭、美妆、健身"),
        ("travel", "出门·见闻", "旅行、旅居、异国观察"),
        ("daily",  "日常·闲聊", "vlog、生活记录、吐槽"),
    ],
    "film": [
        ("jie",   "影视·解说", "电影剧集的解说、深度解析、影评盘点"),
        ("ani",   "动画·番剧", "新番点评、漫画解说、动画杂谈、音声与虚拟主播"),
        ("music", "音乐·演奏", "演奏、翻唱、乐评、音乐史"),
        ("zip",   "译制·搬运", "字幕组、自译、中配、纪录片搬运"),
        ("meme",  "鬼畜·整活", "鬼畜、搞笑剪辑、颜值合集"),
    ],
}

# 看得出属于这一大类、但看不出具体哪一支的人，落到这里。
# 生活选「日常」是因为它本来就是最松的一支；影像选「影视·解说」是因为
# 这一支占了整个类的一半，判不出细分的人多半就是没写清楚的说片号。
FALLBACK_SUB = {"life": "daily", "film": "jie"}

# 子类判据。文本 = 名字 + 签名 + 最近几条标题，一起看。
LIFE_SUB_RULES = [
    ("food",   r"美食|做饭|菜谱|厨房|厨艺|厨师|大厨|烹饪|探店|吃货|好吃|干饭|食堂|"
               r"餐厅|料理|烘焙|面点|家常菜|宴|食材|味道|下厨|三餐|减脂餐|舌尖|"
               r"碳水|炖|炒|烤|锅"),
    ("craft",  r"手工|手作|DIY|diy|修复|翻新|积木|乐高|模型|改造|维修|收纳|整理|"
               r"木工|皮具|编织|陶艺|居家|小妙招|生活小|开箱组装|拼装"),
    ("visual", r"摄影|拍照|相机|镜头|构图|风光|风景|绘画|画师|插画|水彩|油画|素描|"
               r"地理|地图|星空|延时|影像|色彩|设计稿|视觉"),
    ("style",  r"穿搭|美妆|化妆|护肤|健身|减肥|瑜伽|体型|身材|搭配|发型|球鞋|衣柜|"
               r"ootd|OOTD|变美"),
    ("travel", r"旅行|旅游|出行|走遍|环球|见闻|异国|徒步|自驾|旅居|游记|在路上|"
               r"世界尽头|打卡地"),
    # 「记录」单独放会误中「书摘记录」「游戏记录」，所以只认「生活记录」这种组合
    ("daily",  r"日常|vlog|Vlog|生活记录|随拍|碎片|杂谈|吐槽|搞笑|沙雕|整活|"
               r"摆烂|上班|下班"),
]

# 影像·音乐这一支。顺序同样是刻意的：
#   「字幕组」「熟肉」只属于译制的人，最专指，放最前；
#   「鬼畜」「整活」也是自报家门；
#   动画排在影视前面，因为柯南、死神的解说标题里常带"剧场版""电影版"；
#   音乐排在影视前面 —— 试过反过来，弹吉他的、做乐评的会被"看完""一口气"抢走；
#   影视垫底，"影视"两个字出现在分区名里，说明这个人自己填的就是影视。
FILM_SUB_RULES = [
    ("zip",   r"字幕组|中字|熟肉|生肉|自译|中配|译制|双语|汉化|搬运|听译|"
              r"纪录片|纪实|BBC|NHK"),
    ("meme",  r"鬼畜|整活|恶搞|做鬼畜"),
    ("ani",   r"番剧|新番|动漫|动画|漫画|手书|虚拟主播|VUP|vup|音声|声优|作画|"
              r"柯南|死神|海贼|火影|咒术|进击|间谍过家家|奥特曼|特摄|Galgame|galgame|"
              r"京阿尼|蜡笔小新|辛普森|罗小黑"),
    ("music", r"音乐|演奏|钢琴|二胡|吉他|翻唱|歌曲|金曲|合唱|乐评|专辑|旋律|"
              r"演唱|MV|Lofi|lofi|伴奏|唱片|编曲|作曲|交响|歌剧|民乐|Cover|cover"),
    ("jie",   r"电影|影评|解说|解析|精讲|盘点|一口气|看完|豆瓣|烂片|预告|幕后|"
              r"美剧|英剧|韩剧|国剧|日剧|票房|剧场版|剧集|说片|看片|影视"),
]


def sub_of(cat, name, sign, titles):
    """带二级分类的大类内部分流。判不出来就返回 None —— 不要兜底。

    life 这里以前是 return "daily"，结果「日常」又变成了新的垃圾桶：33 个
    连标题都没有的号全堆进去，跟拆之前没区别。宁可留空，让上层决定怎么办。
    """
    rules = {"life": LIFE_SUB_RULES, "film": FILM_SUB_RULES}.get(cat)
    if not rules:
        return None
    text = f"{name} {sign} {' '.join(titles[:4])}".strip()
    if len(re.sub(r"\s", "", text)) < 4:
        return None                      # 一个字都没有，别猜
    for sid, pat in rules:
        if re.search(pat, text):
            return sid
    return None


def life_sub(name, sign, titles):
    return sub_of("life", name, sign, titles)

# ---------------------------------------------------------------- 分区名 -> 类
# 分区名来自 rank.py 抓到的 tid->tname 对照表
TNAME_RULES = [
    # 顺序即优先级：资讯区下的"社会/热点"要先于"社科"匹配，否则新华社、人民日报
    # 这种会被"社科·法律·心理"以外的规则抢走
    (r"^(资讯|热点|时政|社会)$",                        "news"),
    (r"人文历史",                                      "history"),
    (r"财经商业",                                      "biz"),
    (r"社科|法律|心理",                                "ideas"),
    (r"科学科普|科工机械|计算机|软件应用|数码|极客|机械|野生技能|设计|科技", "sci"),
    (r"校园学习|职业职场|公开课|考研|外语",              "study"),
    (r"读书|文学|书评|有声书",                          "books"),
    (r"影视|电影|剧|短片|小剧场|纪录片|综艺|预告",        "film"),
    (r"音乐|演奏|翻唱|乐评|MV|VOCALOID|音MAD|电台|MV",   "film"),
    (r"动画|番剧|国创|MAD|MMD|手书|GMV|特摄|鬼畜|漫",     "film"),
    (r"娱乐|明星|颜值|仿妆|cos",                        "film"),
    (r"游戏|电竞|桌游|音游",                            "game"),
    (r"美食|美食制作|美食侦探|野食",                      "life"),
    (r"手工|绘画|摄影|三农|家居|田园|亲子|运动|健身|体育|"
     r"动物|喵星人|汪星人|萌宠|美妆|穿搭|时尚|舞蹈|汽车|新能源", "life"),
]

# 这几个分区名是 B 站投稿页的默认值，不是内容分类。
# 「日常」尤其离谱：没选分区的人全落在这里，讲投资的、讲键政的、玩游戏的都有。
# 以前把它直接映射成 life，等于让这 20 人在完全不看内容的情况下定了终身。
# 遇到它们就放过，继续往下走名字和关键词判断。
VAGUE_TNAME = re.compile(r"^(日常|搞笑|出行|三农|生活|其他)$")

# ---------------------------------------------------------------- 关键词
KW = {
    "news": ["时政", "时事", "新闻", "日报", "新华社", "央视", "半月谈", "共青团",
             "环球时报", "观察者网", "澎湃", "南方周末", "财新", "记者", "媒体",
             "评论员", "国际关系", "外交", "地缘", "政策", "民生", "社会问题",
             "公共事件", "通报", "调查报道", "党史", "政务", "人大", "政协",
             "工会", "抗议", "社会运动", "移民", "种族", "福利制度", "选举", "议会",
             "键政", "左翼", "右翼", "左派", "右派", "民粹", "政党", "派系",
             "法治", "民主", "宪法", "政体", "政治"],
    "biz": ["财经", "投资", "股票", "基金", "金融", "经济学", "商业模式", "创业",
            "公司", "市值", "财报", "价值投资", "复利", "巴菲特", "芒格", "货币",
            "通胀", "利率", "楼市", "房价", "职场", "产业链", "出海", "关税",
            "贸易战", "银行", "债务", "经济周期", "企业家", "理财", "估值", "风投",
            "就业", "工资", "养老金", "少子化", "生育率", "劳动力", "门店",
            "消费", "裁员", "失业", "制造业", "企业", "生意", "利润"],
    "history": ["历史", "史学", "史料", "古代", "朝代", "先秦", "春秋", "战国",
                "秦汉", "三国", "魏晋", "隋唐", "唐宋", "元朝", "明朝", "清朝",
                "民国", "北洋", "抗战", "二战", "一战", "冷战", "苏联", "俄国",
                "罗马", "希腊", "中世纪", "文艺复兴", "考古", "文物", "通史",
                "帝国", "王朝", "革命史", "长征", "军事史", "战争史", "世界史",
                "中国史", "欧洲史", "日本史", "美国史", "殖民", "古代史"],
    "ideas": ["哲学", "思想", "社会学", "人类学", "心理学", "精神分析", "理论",
              "主义", "伦理学", "政治学", "学术", "论文", "学者", "教授", "讲座",
              "思辨", "观念", "启蒙", "现代性", "后现代", "福柯", "韦伯", "马克思",
              "尼采", "康德", "黑格尔", "现象学", "结构主义", "文化研究", "传播学",
              "媒介", "修辞", "话语", "人文社科", "读书会", "思想史", "访谈", "对谈"],
    "sci": ["科学", "科普", "物理", "化学", "生物", "数学", "天文", "宇宙", "量子",
            "相对论", "医学", "医生", "解剖", "神经", "基因", "细胞", "免疫",
            "药物", "临床", "工程", "机械", "芯片", "半导体", "计算机", "程序员",
            "算法", "人工智能", "软件", "数码", "手机", "测评", "相机", "望远镜",
            "自然", "动物", "植物", "地理", "地质", "气象", "环保", "能源",
            "核", "实验", "研究院", "院士", "极客", "代码", "开源", "科技"],
    "books": ["读书", "书评", "阅读", "书籍", "有声书", "文学", "小说", "诗歌",
              "诗词", "古籍", "经典", "讲书", "书店", "出版社", "作家", "译文",
              "翻译", "散文", "随笔", "书单", "荐书", "导读", "夜读", "朗读",
              "诵读", "听书", "推理", "科幻", "诗话", "词话"],
    "film": ["电影", "影视", "剧集", "影评", "解说", "纪录片", "动漫", "动画",
             "番剧", "音乐", "歌曲", "钢琴", "演奏", "翻唱", "字幕组", "剧场",
             "短片", "预告", "综艺", "明星", "娱乐", "剪辑", "混剪", "MAD",
             "改编", "原声", "配乐", "影迷", "观影", "美剧", "英剧", "韩剧",
             "港片", "漫画", "插画", "扒舞", "舞蹈"],
    "game": ["游戏", "单机", "手游", "网游", "电竞", "主机", "Steam", "Minecraft",
             "我的世界", "桌游", "战雷", "策略游戏", "模拟经营", "RPG", "FPS",
             "音游", "红石", "开黑", "玩家", "实况", "攻略", "抽卡",
             "沙盒", "战棋", "P社"],
    "study": ["考研", "高考", "考公", "四六级", "英语", "日语", "学习", "课程",
              "教程", "网课", "教师", "中学", "高中", "初中", "大学", "辅导",
              "资料", "上岸", "笔记", "讲义", "背诵", "单词", "口语", "备考",
              "自考", "教材", "学校", "学子", "课堂", "刷题", "真题", "教学",
              "讲题", "补习", "语言", "双语"],
    "life": ["美食", "做饭", "菜谱", "厨房", "厨艺", "探店", "吃货", "旅行",
             "旅游", "风景", "摄影", "手工", "修复", "萌宠", "家居", "穿搭",
             "化妆", "美妆", "健身", "减脂", "好物", "收纳", "装修", "露营",
             "钓鱼", "田园", "三农"],
}

# 名字里出现就等于身份自述，直接定
HARD = [
    (r"字幕组|译制|翻译组", "film"),
    (r"博物馆|博物院|国家宝藏", "history"),
    (r"有声书|听书|夜读|诵读", "books"),
    (r"讲史|说史|通史|史馆|史图|历史档案馆", "history"),
    (r"考研|高考|教语文|教数学|教英语|讲语文", "study"),
    (r"哲学|社科", "ideas"),
]

# 机构号：身份写在名字里，但发的内容版块很杂（新华社被 B 站分到"日常"、
# 半月谈被分到"财经商业"），所以这些必须排在投稿分区之前判
HARD_FIRST = [
    (r"新华社|人民日报|半月谈|环球时报|观察者网|网易新闻|澎湃新闻|南方周末|"
     r"中国日报|光明日报|共青团|央视新闻|央视网", "news"),
    (r"国家博物馆|故宫博物院|国家宝藏", "history"),
    (r"出版社", "books"),
]

# 人工指定：分区信号拿不到（75 天没投稿）、关键词也判不出来的，按名单逐个人工定。
# 优先级最高——分区判错的几个（比如黑纹白斑马被 B 站分到"改装玩车"）也钉在这。
MANUAL_BY_CAT = {
    "film": [
        13300533,    # 赛琪SAIKI 翻唱
        32708462,    # 不动的ACG大图书馆
        2859372,     # 名作之壁吧 动画销量
        347158173,   # 小梦是小少爷 音乐
        672877136,   # 小夜予- 扒舞
        692364976,   # 法国二胡骑士Eliott
        694592246,   # 苦瓜咖喱 AI 音乐
        355294965,   # 距离厘米远 柯南
        5024187,     # 龙三条 影视合集
        23561840,    # 荐影希米格
        3493128782744453,  # 42手册 影视
        3546755815704608,  # 小书子guitar
        1524009,     # 砂纸大魔王 动画
        24957521,    # 吃肉包的怂鼠 配音
        3546639002241255,  # Sisi谈琴
        32172110,    # 京阿尼语料
        440121192,   # JLRS日落fm 欧美音乐
        179253021,   # 银翼杀手俊达萌 译制片
        7957044,     # 我是十四哦丶 影评
        1263732318,  # 黑纹白斑马 搬运（B站挂在"改装玩车"，明显不对）
        2024416529,  # 最佳金曲
        3461571332279149,  # 米花小侦探
        3493258879567957,  # 佩奇说剧啦
        427652006,   # 柚卡yk 动漫游戏
        1954521410,  # 儿童合唱团OneVoice
        17223352,    # 1900影剧室
        23455732,    # 神奇的大智 动画
        927587,      # 木鱼水心
        389087379,   # 草泥泥影院
        411541411,   # 萌豆说片
    ],
    "books": [
        181699543,   # 一个无聊的无聊人 小说朗读
        4107705,     # CaaaaaR 荐书
        74161368,    # 掺酒_Crane 推理说书
        1321291782,  # 轻之国度NACG
        525081796,   # 空空猫和思卡尔 读书播客
        5669044,     # 藏书票EX-LIBRIS
        18665912,    # 醒醒苏醒醒 文学
        183724978,   # 聆听中心ListenLit
        101653947,   # 有书快看
        1264844013,  # 后台播放台 听书
        346667461,   # 异本正经Studio 推理播客
        2047131,     # 读客熊猫君 出版社
        2019838649,  # 恩宇书
        538592249,   # 华夏传书 古籍
        1867363969,  # 歐麗娟 红楼梦
        321135684,   # 5分钟快读
        1468276679,  # 夜月枕前书
    ],
    "history": [
        388782740,   # 夜行微光
        1824543376,  # 姬为毅 大周八百年
        1433934891,  # 纪不住
        34264408,    # 御史房
        1340705215,  # 瑞士卫队 大航海
        255499162,   # DC大成工坊 历史科普
        19852864,    # 星火零字班
        414116896,   # 伊丽莎白卡芙琳 军事史
        43664251,    # 吟游诗人袁同学
        3493272628496719,  # 白驹岁月 外国史
        26194490,    # 小森森2 中国通史
        353128526,   # 米海尔 罗马地图
        544291240,   # 东兴苟十三
        1362319776,  # 方志远讲明史
        13680050,    # Grandpagu顾爷 艺术史
        3493358,     # 益逹诺夫斯基 俄国社会史
        1975993453,  # 皮哥研究社
        7481602,     # 安州牧
        483784677,   # 嘉祐生宣
        33397277,    # 有点意思研究所
        23947287,    # 小约翰可汗
        37388663,    # 布哈林-R
        88704528,    # 网易公版影像
        407863735,   # 欣阳漫话
    ],
    "ideas": [
        3546799388232614,  # 毕英杰Johnathan 人文与科技
        5798089,     # 槛外人philosophy
        43112644,    # 十三邀
        1871365234,  # 大问题Dialectic
        237673207,   # 张言Yann 心理学
        13736113,    # 有机社会
        93796936,    # 混乱粉笔
        6690427,     # 余风同学 人类学
        620373859,   # 光鸿月 道家
        1388499326,  # 知识放映室
        1266383426,  # 主持人王宁 访谈
        390371228,   # 知心者联盟
    ],
    "sci": [
        12566101,    # 秋葉aaaki AI 绘画工具
        3706926953138884,  # MioriaAI
        254120073,   # 机制派 普林斯顿博士
        72270557,    # 芳斯塔芙
        88461692,    # 3Blue1Brown
        26798384,    # 柴知道
        511148568,   # 冷却报告
        483649695,   # 神挖坑 地图地理
        442603088,   # 三维地图看世界
        482469389,   # 何解毒 人文地理
        388094867,   # 微观小卡
        254463269,   # 毕导
        1195284234,  # 李白尼的猜想
        6842218,     # 小先生 性教育科普
        164932365,   # 门捷列夫斯基 化学
    ],
    "game": [
        90580766,    # 下木枕流 策略游戏模组
        11135875,    # 赛万味超人
        1713219676,  # 哈喽阿伟 桌游
        21302388,    # 楚门plus
        13817466,    # 菠萝柚王子
        17215659,    # 飞天炸鸡XD
        280480736,   # 鱼子酱 饥荒
        17422736,    # 阳光的酥皮 游戏资源
        11546472,    # 雪栗星貉
        11602644,    # WuGuangYao Minecraft
    ],
    "biz": [
        46405906,    # 阿周的Eureka 经济学
        416987337,   # 东京老萧 财经城市
        3546390716221853,  # 听書致富
        649109545,   # 一书一课 职场
        243198296,   # 花火的钞票百科
    ],
    "study": [
        18916827,    # 博士阿辉 方法教程
        403434719,   # JustinEnglish 英语（B站挂"篮球"分区，不对）
        3461567865686420,  # galw朝阳门
        16574832,    # 人人词典
        63274187,    # dc008 课件
        511582360,   # 酸辣土土 作文素材
    ],
    "news": [
        402595174,   # today视界 影像记录中国
    ],
}
MANUAL = {mid: cid for cid, mids in MANUAL_BY_CAT.items() for mid in mids}

# 第二轮：分区信号判错、但关键词也没抓住的。分区是 UP 自己选的，有时跟内容无关
# （维特根斯坦酱挂在"人文历史"下讲哲学，卡尔维诺不下树挂在影视区讲文学）
MANUAL_FIX = {
    "ideas": [
        4563012,     # 维特根斯坦酱 分哲
        98834057,    # 煮酒gyp 人文社科
    ],
    "books": [
        47782841,    # 乌兰巴乔夫 俄国文学
        3546957419120860,  # 廿肆声 古诗词
        1315696773,  # 韩潇老师讲诗词
        381859503,   # 天天读书会
        398926744,   # 智富读书会
        3494360878418559,  # 林语诵读
        1316846900,  # 香山夜读
        283195854,   # 小说家Puckio
        495224316,   # 一只萧包子
        9303228,     # 江湖举人
        3493292719213325,  # 忠爱历史有声书
        329598489,   # 卡尔维诺不下树
        298373650,   # 推理派
        1116632922,  # 岛游Aurora-推理
    ],
    "life": [
        399698329,   # 阿祖尔Azure 画师
        500924033,   # 顾得热榜 消费品测评
        1532165,     # papi酱
        587004949,   # 摄影怪兽 摄影
        478192872,   # 泰菲解说游戏 名字还叫解说游戏，近一年全是旅行 vlog
    ],
    "study": [
        1336191464,  # 听懂英语快讯
    ],
    "sci": [
        14583962,    # 思维实验室
        1121931859,  # 泛科学院
    ],
    "game": [
        305574450,   # 暴走的小脑斧_ 策略游戏
        2108092119,  # 我的世界凯文解说
        122575238,   # 怠惰の守夜
        1265680561,  # 永雏塔菲
    ],
    "history": [
        55895377,    # 爱奥尼尤斯Aionius 废奴史
    ],
}
MANUAL.update({mid: cid for cid, mids in MANUAL_FIX.items() for mid in mids})

# 注意：这个 dict 里同一个类别只能出现一次，后面再写一遍 "books" 会把前面整块覆盖掉
MANUAL.update({
    405054588: "ideas",             # TED精选演讲
    3546872847272223: "life",       # 一分钟家事研究所 生活技巧
    22121599: "film",               # 纪录片之家字幕组
    14427045: "film",               # M的蒙太奇笔记 电影×社会学
    160417017: "biz",               # Springfield1999 经济学
    3546390143699831: "books",      # 每天听学人
})

# 第三轮：复查「生活·杂览」时揪出来的。原来这个类靠「日常」这个万能分区
# 和几个泛词（日常/生活/vlog）兜底，等于什么人都能进。逐条看内容之后钉正。
MANUAL.update({
    # —— 讲政治与社会议题的，跟生活无关 ——
    3690991867398897: "news",       # 修路研究所 巴西大选、全球左翼网络
    646371951: "news",              # 手中大王 欧盟法治短片
    2211143: "news",                # 谭普罗 键政观察室、西方左翼观察
    # —— 产业与投资 ——
    36116130: "biz",                # 代数学家89 日本失去的三十年、派遣工
    383587023: "biz",               # 复利的奇迹价值投资
    3494361167824919: "biz",        # 言究院YanLabs 芒格投资建议（原来在生活）
    76962520: "biz",                # 减肥秃了头 蔡司光刻机、烘焙店倒闭
    # —— 游戏 ——
    5682878: "game",                # 丝Silk 策略模拟、卡牌生存
    2315992: "game",                # 北眺 Suzerain、王国风云3 模组
    11858718: "game",               # chshh 碧蓝航线
    587004949: "game",              # 摄影怪兽 名字叫摄影，内容全是十字军之王3
    567832333: "game",              # 海兔喵斯拉 原创桌游
    # —— 读书 ——
    32680948: "books",              # 与书无关 出版社书评
    3494362008783640: "books",      # 睡前读本书
    384995067: "books",             # 万卷丨诗词
    1115852727: "books",            # 霸天硬核读书
    391366781: "books",             # 大罗小罗自习室 读书日常
    # —— 心理与社科 ——
    514172689: "ideas",             # 心理拾光 社交与心理
    # —— 科技与科普 ——
    1254493743: "sci",              # KMN放映室 射电望远镜、刚果河地理
    3513872: "sci",                 # 灰森森森 AI 绘图、DeepSeek
    500924033: "sci",               # 顾得热榜 家电数码测评
    # —— 影视音乐动画 ——
    696252484: "film",              # 公孙步 辛普森一家
    502970: "film",                 # 冲浪误入深水区 自制熟肉
    628499561: "film",              # 生活录像馆 Lofi 音乐
    21649854: "film",               # 贞叶 音声剧情
    23506535: "film",               # Kazimas 音声剧情
    1102228097: "film",             # 小白爆肝队 剧集整活解说
    490802660: "film",              # 神颜部 颜值合集
    # —— 历史 ——
    629341851: "history",           # 照花荣 明清微观史
    # —— 复查时归错了类的，内容其实在别的板块 ——
    20423048: "books",              # 书房连着厨房 名字像做饭，内容是苏童小说讲读
    49613536: "books",              # 老迷路讲创作 解析《活着》
    488656343: "books",             # 半生脱缰 史达祖词
    70903428: "books",              # 淋雨の伞 书摘
    60509716: "books",              # 一个爱吃的猪 诗词背诵
    9654755: "film",                # 海边的克利夫兰 美剧自译
    22460916: "film",               # eskus SNL 中字
    102065263: "film",              # 我是2070 辛普森逐集解析
    57676402: "film",               # 超音速君 鬼畜
    452575497: "film",              # 我叫机某人 神作盘点
    595407557: "film",              # 菜菜子Nanako 虚拟主播
    419425: "film",                 # 风野社长 动漫电影游戏杂谈
    347472582: "game",              # Nonex0 我的世界
    37783541: "game",               # 可靠的KV--2 战争雷霆
    400206053: "news",              # 秋叶蓝卜城 太原接送、高铁通车这类社会观察
    # —— 确实是生活，只是被「日常」分区判丢了子类 ——
    1343321779: "life",             # 央视农业 三农
    1844521460: "life",             # 桂月july绘 画师
    284720235: "life",              # 小古KK 美妆测评
    32820037: "life",               # 歪果仁研究协会 老外导游
    406636263: "life",              # HOLA小测佬 环球旅行
    91726868: "life",               # 田宇青 城市生活
    256237759: "life",              # 我叫小渔人 梗与整活，不是做饭
    544503411: "life",              # 沙琪玛er 旅行笔记
    478192872: "life",              # 泰菲解说游戏 名字是旧身份，近一年全是旅行 vlog
    # —— 子类又分错的一批 ——
    173817396: "ideas",             # L君杂谈 借《浪潮》讲群体心理
    28053896: "game",               # 吉儿一范伦婷 我的世界服务器建城
    399698329: "film",              # 阿祖尔Azure 二次元音声剧情
    96609715: "film",               # 小黑屋的貘 CreepyPasta 恐怖故事
})

# 生活这一支的子类，人工指定。规则判不出来的才写在这。
MANUAL_SUB = {
    3546872847272223: "craft",      # 一分钟家事研究所 居家维修
    1532165: "daily",               # papi酱
    1343321779: "food",             # 央视农业
    256237759: "daily",             # 我叫小渔人 整活玩梗，原来照着名字错判成做饭
    284720235: "style",             # 小古KK
    1844521460: "visual",           # 桂月july绘
    32820037: "travel",             # 歪果仁研究协会
    406636263: "travel",            # HOLA小测佬
    544503411: "travel",            # 沙琪玛er
    478192872: "travel",            # 泰菲解说游戏
    91726868: "daily",              # 田宇青
}

# 影像·音乐这一支的子类，人工指定。规则能判对八成，剩下这两成是
# 「名字和标题都对不上内容」的人：音声剧、虚拟主播、搬运号自称"中字"之类的。
MANUAL_SUB.update({
    # 音声剧、虚拟主播 —— 规则只看名字抓不到
    13300533: "ani",                # 赛琪SAIKI 直播与音声
    595407557: "ani",               # 菜菜子Nanako 虚拟主播
    399698329: "ani",               # 阿祖尔Azure 音声剧情
    21649854: "ani",                # 贞叶 音声剧情
    347158173: "ani",               # 小梦是小少爷 音声
    427652006: "ani",               # 柚卡yk 游戏动画短片
    32172110: "ani",                # 京阿尼语料 自制续作（签名里写中字）
    538662399: "ani",               # 枫和日丽 柯南情报
    # 音乐 —— 被"看完""一口气""影视"这些词抢走
    672877136: "music",             # 小夜予- 路演与舞台翻跳
    3546755815704608: "music",      # 小书子guitar 弹唱
    179253021: "music",             # 银翼杀手俊达萌 英文翻唱
    71573495: "music",              # 北流嘉措 轻音乐（原来被"轻音"误中动画）
    # 影视解说 —— 讲动画、讲漫画，但本体还是说片
    23561840: "jie",                # 荐影希米格 金田一推理剧
    669154966: "jie",               # 椒盐说影
    604710494: "jie",               # 长片短解
    493408382: "jie",               # 大鸡电影
    23455732: "jie",                # 神奇的大智 快速看完
    56274025: "jie",                # 电影幕后故事 名场面盘点
    1102228097: "jie",              # 小白爆肝队 剧集整活解说
    17223352: "jie",                # 1900影剧室 影视金曲+八卦
    # 鬼畜·整活
    57676402: "meme",               # 超音速君 鬼畜
    39337803: "meme",               # 痕继痕迹 AI 鬼畜
    490802660: "meme",              # 神颜部 颜值合集
    5024187: "meme",                # 龙三条 整活补档
    24957521: "meme",               # 吃肉包的怂鼠 配音整活
    1524009: "meme",                # 砂纸大魔王 整活
    84519089: "meme",               # 窃格王拉 整活
    520764049: "meme",              # WalkerKris AI 对决
    # 译制·搬运
    521797036: "zip",               # Epentibi 伪新闻影像存档
})

# 签名里这些片段是商务联系方式，先切掉再参与判断
NOISE = re.compile(
    r"商务|合作|微信|vx|VX|V信|QQ|qq|群号|粉丝群|交流群|催更群|邮箱|公众号|私信|"
    r"充电|淘宝|小店|橱窗|店铺|链接|https?://|www\.|@|回复|领取|获取|加群|群：")


def clean_sign(s):
    """签名按标点切句，含联系方式的句子整句丢掉"""
    if not s:
        return ""
    parts = re.split(r"[|｜/、,，。;；!！\n]", s)
    keep = [p.strip() for p in parts
            if p.strip() and not NOISE.search(p) and not re.fullmatch(r"[\d\s:：+\-]*", p)]
    return " ".join(keep)[:140]


def tname_to_cat(tname):
    for pat, cid in TNAME_RULES:
        if re.search(pat, tname or ""):
            return cid
    return None


def load_titles(conn, mid):
    rows = conn.execute(
        "SELECT title FROM videos WHERE mid=? ORDER BY pub_ts DESC LIMIT 4", (mid,)).fetchall()
    return [r[0] for r in rows if r[0]]


# 泛词：单独出现说明不了什么，权重打对折。
# 「手机」两个字在标题里一出现就把人推进科学，「历史」「文学」同理——
# 可这些词在生活、影视、读书的标题里到处都是。复查「生活」时发现
# 有四个人光凭标题里的「手机」被判进了科学·技术。
WEAK = set("""
手机 测评 历史 文学 动漫 游戏 解说 实验 软件 新闻 笔记 背诵 语言 资料
课堂 学校 教室 老师 分享 记录 世界 中国 人类 日本 美国 欧洲 亚洲 文化
社会 教育 学习 工作 大学 电影 音乐 动画 漫画 小说 诗歌 经济 金融 公司
科技 科学 技术 研究 理论 思想 哲学 心理 评论 访谈 纪录片 短片 综艺
娱乐 明星 视频 生活 日常 现代 传统 经典 故事
""".split())


def kw_score(name, sign, titles):
    sc, hit = {}, {}
    for cid, kws in KW.items():
        s, h = 0, []
        for kw in kws:
            w = 0.5 if kw in WEAK else 1.0
            if kw in name:
                s += 4 * w
                h.append("名:" + kw)
            if kw in sign:
                s += 1 * w
                h.append("签:" + kw)
            for t in titles:
                if kw in t:
                    s += 2 * w
                    h.append("题:" + kw)
                    break
        sc[cid], hit[cid] = s, h
    best = max(sc.items(), key=lambda kv: kv[1])
    return best[0], best[1], hit[best[0]]


def audit(conn, result, raw):
    """跑完再用「最近六条标题」反查一遍，把对不上的点出来。

    人工表是某一刻的判断，人却会换内容：泰菲解说游戏 名字还写着游戏，
    近一年全是旅行 vlog；我叫小渔人 名字像赶海做饭，实际在玩梗。这类
    错法规则自己发现不了，只能靠事后比对。这里只报警、不改结果。
    """
    name_of = {u["mid"]: u["name"] for u in raw}
    sign_of = {u["mid"]: clean_sign(u.get("sign") or "") for u in raw}
    bad = []
    for mid, v in result.items():
        cat = v.get("cat")
        if cat in ("friend", "unknown"):
            continue                      # 熟人按分组定，待定本来就没结论
        titles = [r[0] for r in conn.execute(
            "SELECT title FROM videos WHERE mid=? ORDER BY pub_ts DESC LIMIT 6",
            (mid,)).fetchall() if r[0]]
        if len(titles) < 3:
            continue
        best, s, hit = kw_score(name_of.get(mid, ""), sign_of.get(mid, ""), titles)
        if best != cat and s >= 6:
            bad.append((s, name_of.get(mid, ""), cat, best,
                        hit[0] if hit else "", titles[0][:30]))
    if not bad:
        return
    bad.sort(reverse=True)
    print(f"\n体检：{len(bad)} 个人的分类跟最近六条标题对不上，值得翻一眼")
    for s, nm, cat, best, h, t in bad:
        print(f"  {s:4.1f}  现在 {cat:<8} 像 {best:<8} {nm[:16]:18s} {h} | {t}")


def main():
    raw = json.load(open(os.path.join(DATA, "ups_raw.json"), encoding="utf-8"))
    area = json.load(open(os.path.join(DATA, "up_area.json"), encoding="utf-8"))
    rank = json.load(open(os.path.join(DATA, "rank.json"), encoding="utf-8"))
    tidmap = rank.get("tidmap") or {}

    conn = sqlite3.connect(os.path.join(DATA, "biliplayer.db"))
    result = {}
    stat = {"by_area": 0, "by_hard": 0, "by_kw": 0, "by_manual": 0, "by_friend": 0,
            "by_group": 0, "by_life": 0, "unknown": 0}
    no_area = []
    pending = []        # 前三路都没判出来的，等第二轮用关注分组定

    def put(mid, cid, why, **kw):
        """带二级分类的大类，顺手把分支算出来"""
        sub = ""
        if cid in SUBCATS:
            # 投稿分区名也算一条证据：「动漫杂谈」「翻唱」「影视剪辑」这种是他自己填的
            titles = ([kw["area"]] if kw.get("area") else []) + list(kw.get("titles", []))
            sub = MANUAL_SUB.get(mid) or sub_of(
                cid, kw.get("name", ""), kw.get("sign", ""), titles) or ""
            if not sub:
                sub = FALLBACK_SUB[cid]   # 看得出是这个大类、看不出哪一支的落这儿
        kw.pop("name", None)
        kw.pop("sign", None)
        kw.pop("titles", None)
        d = {"cat": cid, "why": why}
        if sub:
            d["sub"] = sub
        d.update(kw)
        result[mid] = d
        return d

    for u in raw:
        mid = u["mid"]
        name = re.sub(r"\s+", " ", u["name"] or "").strip()
        sign = clean_sign(u["sign"])
        tags = u.get("tag") or []
        titles = []

        if 504992 in tags:
            put(mid, "friend", "原学校分组")
            stat["by_friend"] += 1
            continue
        if mid in MANUAL:
            put(mid, MANUAL[mid], "人工", name=name, sign=sign,
                titles=load_titles(conn, mid))
            stat["by_manual"] += 1
            continue

        # 0. 机构号身份
        hf = next((c for pat, c in HARD_FIRST if re.search(pat, name)), None)
        if hf:
            put(mid, hf, "机构身份")
            stat["by_hard"] += 1
            continue

        # 1. 投稿分区
        a = area.get(str(mid)) or {}
        tn = tidmap.get(str(a.get("tid"))) or a.get("tname") or ""
        if tn and not VAGUE_TNAME.match(tn):
            cid = tname_to_cat(tn)
            if cid:
                put(mid, cid, "投稿分区：" + tn, area=tn, name=name, sign=sign,
                    titles=load_titles(conn, mid))
                stat["by_area"] += 1
                continue
        elif tn:
            # 「日常」这种泛分区：记一笔，但不当依据
            no_area.append((mid, name, tn))

        # 2. 名字自述
        hard = next((c for pat, c in HARD if re.search(pat, name)), None)
        if hard:
            put(mid, hard, "名字自述", name=name, sign=sign,
                titles=load_titles(conn, mid))
            stat["by_hard"] += 1
            continue

        # 3. 关键词
        titles = load_titles(conn, mid)
        cid, s, hit = kw_score(name, sign, titles)
        if s >= 2:
            put(mid, cid, ",".join(hit[:3]), score=s, name=name, sign=sign, titles=titles)
            stat["by_kw"] += 1
            continue

        pending.append((mid, name, sign, titles, tags, s))

    # ---- 第二轮：拿关注分组反推 ----
    # 你自己在 B 站给关注人分的组是有内容的（一群讲史的、一群搞医学的）。
    # 先看每组的「已定人员」多数落在哪一类，再用这个多数去判组里没信号的那些。
    # 门槛：组里至少 5 个已定的人，且多数派占 45% 以上，否则这个组算杂的，不用。
    vote = {}
    for u in raw:
        r = result.get(u["mid"])
        if not r:
            continue
        for t in (u.get("tag") or []):
            d = vote.setdefault(t, {})
            d[r["cat"]] = d.get(r["cat"], 0) + 1

    TAG_MEANING = {}
    for t, d in vote.items():
        n = sum(d.values())
        if n < 5:
            continue
        cid, cnt = max(d.items(), key=lambda kv: kv[1])
        if cid in ("friend", "unknown") or cnt / n < 0.45:
            continue
        TAG_MEANING[t] = (cid, cnt, n)
    print("关注分组的主语义：")
    for t, (cid, cnt, n) in sorted(TAG_MEANING.items(), key=lambda kv: -kv[1][2]):
        print(f"  {t:<12} -> {cid:<8} {cnt}/{n}")

    for mid, name, sign, titles, tags, s in pending:
        # 顺序要紧：内容像什么 > 你在 B 站把他放哪个组。
        # 分组是你几年前分的，内容是他现在在发的。5-MinuteCrafts中文 签名里
        # 就写着「DIY 手工频道」，被科学组拉走显然不对，所以内容先判。
        sub = life_sub(name, sign, titles)
        if sub:
            result[mid] = {"cat": "life", "sub": sub, "why": "内容像生活", "score": s}
            stat["by_life"] += 1
            continue

        picked = next(((t, TAG_MEANING[t]) for t in tags if t in TAG_MEANING), None)
        if picked:
            t, (cid, cnt, n) = picked
            d = {"cat": cid, "why": f"关注分组：同组 {cnt}/{n} 人"}
            if cid in SUBCATS:
                d["sub"] = (MANUAL_SUB.get(mid) or sub_of(cid, name, sign, titles)
                            or FALLBACK_SUB[cid])
            result[mid] = d
            stat["by_group"] += 1
            continue

        # 都不行就老实标「待定」。宁可让用户点一下，也不假装知道他是什么。
        result[mid] = {"cat": "unknown", "why": "没有任何信号", "score": s}
        stat["unknown"] += 1

    # 库里有、但 ups_raw.json 里没有的人（新关注的，或者 follow.py 那次抓漏了），
    # 也得给个说法 —— 否则它在侧栏哪个分类下都找不到，只能从「全部关注」里翻。
    known = {u["mid"] for u in raw}
    for mid, upname in conn.execute("SELECT mid, name FROM ups").fetchall():
        if mid in known or mid in result:
            continue
        titles = load_titles(conn, mid)
        cid, s, hit = kw_score(upname or "", "", titles)
        if s >= 2:
            d = {"cat": cid, "why": "补漏：" + ",".join(hit[:3])}
            if cid in SUBCATS:
                d["sub"] = (MANUAL_SUB.get(mid) or sub_of(cid, upname or "", "", titles)
                            or FALLBACK_SUB[cid])
            result[mid] = d
            stat["by_kw"] += 1
            continue
        sub = life_sub(upname or "", "", titles)
        if sub:
            result[mid] = {"cat": "life", "sub": sub, "why": "补漏"}
            stat["by_life"] += 1
        else:
            result[mid] = {"cat": "unknown", "why": "补漏：不在关注画像里"}
            stat["unknown"] += 1

    audit(conn, result, raw)

    conn.close()

    from collections import Counter
    c = Counter(v["cat"] for v in result.values())
    print("\n分类分布：")
    for cid, nm in CATS:
        print(f"  {nm:10s} {c.get(cid, 0):4d}")
    print("判定依据：", stat)

    for cat in SUBCATS:
        cnt = Counter(v.get("sub") for v in result.values() if v["cat"] == cat)
        total = sum(cnt.values())
        print(f"\n{CAT_NAME[cat]}（{total} 人）的分支：")
        for sid, nm, desc in SUBCATS[cat]:
            print(f"  {nm:<10} {cnt.get(sid, 0):4d}   {desc}")
        lost = total - sum(cnt.get(s[0], 0) for s in SUBCATS[cat])
        if lost:
            print(f"  !! 有 {lost} 人没落到任何一支")

    json.dump(result, open(os.path.join(DATA, "cats.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 特别关注单独存，它是跨分类的标记，不参与分类
    stars = [x["mid"] for x in raw if x.get("special")]
    json.dump(stars, open(os.path.join(DATA, "stars.json"), "w", encoding="utf-8"))
    print(f"\n特别关注 {len(stars)} 人")

    name_of = {x["mid"]: x["name"] for x in raw}
    sign_of = {x["mid"]: x["sign"] for x in raw}
    print(f"\n挂泛分区（日常/搞笑/出行这类）的 {len(no_area)} 个，已转去看内容：")
    for m, n, tn in no_area[:20]:
        print(f"   {tn:8s} | {n[:18]:22s} -> {result.get(m, {}).get('cat', '?')}")

    un = [m for m, v in result.items() if v["cat"] == "unknown"]
    print(f"\n待定（名字签名都没提供线索）的 {len(un)} 个：")
    for m in un[:30]:
        print(f"   {name_of.get(m,'')[:20]:22s} | {' '.join((sign_of.get(m) or '').split())[:36]}")


if __name__ == "__main__":
    main()
