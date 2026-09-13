---
title: 端到端评测：preview2.0.0-r3
date: 2026-09-14
tags:
  - 评测报告
  - issue-37
  - e2e
env: scnet-main-k100
target: preview2.0.0-r3
git: 13cc5a2
---

# 端到端评测：preview2.0.0-r3

> [!abstract] 一句话结论
> 效果：数字保真 ==613 个数字只有 346 个全对（56%，红线 100%）==；每 7 个敏感实体漏 1 个（召回 85.5%）。
> 速度：单页约 ==14 秒==；20 页卷宗约 5 分钟（吞吐 3.9 页/分钟）。
> 稳健性：全部文件跑通，0 失败。
> [!danger] 数字保真未达标：613 个数字只有 346 个全对（56%，红线 100%）（漏脱敏红线）
> 身份证/电话/银行卡这类数字要求==逐字符全对==，本次 613 个数字只有 346 个全对（56%，红线 100%）。
> - 112 条只差空格/连字符（OCR 把数字认串了）→ ==可自动修复==
> - 155 条是真错/真丢 → 需要按层排查（见折叠明细）

> [!warning] 漏检情况：每 7 个敏感实体漏 1 个（召回 85.5%）
> 漏检 = 该脱敏的没被识别 = ==漏脱敏==。 最差的两类：邮箱（34%）、银行卡号（69%）。
> 误检（把无关内容也脱掉）：16%，损害文档可读性。

> [!success] 速度：单页约 14 秒（最慢 10% 的页要 15 秒+）
> 20 页卷宗约 5 分钟（吞吐 3.9 页/分钟）
> 单页时间花在哪（OCR/识别/定位）见折叠的速度分解。


## 各文件快照

> [!example]- 全部文件一览（点开）
>
> | 文件 | 载体 | F1 | 数字保真/结果 | 单页 p50 |
> |---|---|---|---|---|
> | syn_contract_1p_mid | scanned_pdf | 0.889 | ❌ | - |
> | syn_contract_10p_mid | scanned_pdf | 0.883 | ❌ | 13.417s |
> | syn_contract_50p_mid | scanned_pdf | 0.876 | ❌ | 13.253s |
> | syn_judgment_3p_mid | scanned_pdf | 0.774 | ❌ | 15.302s |
> | syn_contract_10p_dense | scanned_pdf | 0.783 | ❌ | 25.919s |
> | syn_contract_10p_sparse | scanned_pdf | 1.000 | ✅ | 6.283s |
> | syn_contract_3p_mid | text_pdf | 0.871 | ❌ | 12.75s |
> | syn_judgment_txt_3p_mid | text_pdf | 0.774 | ❌ | 15.241s |
> | syn_warrant_1p_mid | text_pdf | 0.571 | ✅ | - |
> | syn_hybrid_4p_mid | hybrid_pdf | 0.862 | ❌ | 13.513s |
> | syn_contract_2p_mid | docx | 1.000 | ✅ | 12.898s |
> | syn_judgment_2p_mid | docx | 1.000 | ✅ | 10.095s |
> | syn_contract_txt_1p_mid | txt | 1.000 | ✅ | 6.846s |
> | syn_statement_1p_table | txt | 0.804 | ❌ | 13.904s |
> | syn_edge_3p_mixed | scanned_pdf | 0.727 | ❌ | 16.14s |

> [!example]- 明细：分类型效果（P/R/F1）
>
> | 类型 | P | R | F1 | tp | fp | fn |
> |---|---|---|---|---|---|---|
> | 地址 | 0.7712 | 0.8198 | 0.7948 | 91 | 27 | 20 |
> | 姓名 | 1.0000 | 0.9733 | 0.9865 | 365 | 0 | 10 |
> | 护照号 | 1.0000 | 0.8980 | 0.9462 | 44 | 0 | 5 |
> | 日期 | 0.9789 | 0.9134 | 0.9450 | 232 | 5 | 22 |
> | 机构名称 | 0.6125 | 0.9703 | 0.7510 | 196 | 124 | 6 |
> | 电话 | 0.9886 | 0.7793 | 0.8715 | 173 | 2 | 49 |
> | 身份证号 | 0.9661 | 0.9500 | 0.9580 | 114 | 4 | 6 |
> | 邮箱 | 0.3097 | 0.3431 | 0.3256 | 35 | 78 | 67 |
> | 银行卡号 | 0.8846 | 0.6866 | 0.7731 | 92 | 12 | 42 |
> [!example]- 明细：数字保真分级（exact=全对 / near_miss=只差空格 / miss=真错）
>
> | 类型 | exact | near_miss | miss | 正确率 |
> |---|---|---|---|---|
> | 身份证号 | 114 | 0 | 6 | 95.0% |
> | 护照号 | 35 | 9 | 5 | 71.4% |
> | 电话 | 164 | 9 | 49 | 73.9% |
> | 银行卡号 | 5 | 87 | 42 | 3.7% |
> | 邮箱 | 28 | 7 | 53 | 31.8% |
> - miss [身份证号] '110102196102226774'
> - miss [身份证号] '110133199209258248'
> - miss [身份证号] '110164197804289714'
> - near_miss [护照号] GT='E11773856' → PRED='E11 773856'
> - near_miss [护照号] GT='E13737768' → PRED='E1373 7768'
> - near_miss [护照号] GT='E17174614' → PRED='E 171 74614'
> - near_miss [护照号] GT='E21593416' → PRED='E21 593416'
> - near_miss [护照号] GT='E11282878' → PRED='E1 1 282878'
> - near_miss [护照号] GT='E11773856' → PRED='E 1 1 7 73856'
> - near_miss [护照号] GT='E90773800' → PRED='E 90773800'
> - near_miss [护照号] GT='E91264778' → PRED='E 91 264 778'
> - miss [护照号] 'E78791789'
> - miss [护照号] 'E79282767'
> - miss [护照号] 'E79773745'
> - miss [护照号] 'E80264723'
> - miss [护照号] 'E80755701'
> - near_miss [电话] GT='15102175741' → PRED='1 5102 1 7574 1'
> - near_miss [电话] GT='13823875918' → PRED='1 3823875918'
> - near_miss [电话] GT='16554601165' → PRED='1 6 55 4 601 165'
> - miss [电话] '14470564194'
> - miss [电话] '16562739935'
> - miss [电话] '19378388453'
> - miss [电话] '13492321604'
> - miss [电话] '13747091417'
> - miss [电话] '14115794381'
> - miss [电话] '14200717652'
> - miss [电话] '14385640923'
> - miss [电话] '14861024568'
> - miss [电话] '15584497345'
> - miss [电话] '15839267158'
> - miss [电话] '16038123580'
> - miss [电话] '16123046851'
> - miss [电话] '16207970122'
> - miss [电话] '17676673086'
> - miss [电话] '17931442899'
> - miss [电话] '18300145863'
> - miss [电话] '18654915676'
> - miss [电话] '19023618640'
> - miss [电话] '19768848827'
> - near_miss [银行卡号] GT='6222 0210 1007 2021' → PRED='6222 021010072021'
> - near_miss [银行卡号] GT='6222 0210 1038 2114' → PRED='6222 021010382114'
> - near_miss [银行卡号] GT='6222 0210 1069 2207' → PRED='6222021010692207'
> - near_miss [银行卡号] GT='6222 0210 1100 2300' → PRED='6222 021011002300'
> - near_miss [银行卡号] GT='6222 0210 1131 2393' → PRED='6222 021011312393'
> - near_miss [银行卡号] GT='6222 0210 1162 2486' → PRED='622202101162 2486'
> - near_miss [银行卡号] GT='6222 0210 1193 2579' → PRED='6222 021011932579'
> - near_miss [银行卡号] GT='6222 0210 1224 2672' → PRED='6222021012242672'
> - near_miss [银行卡号] GT='6222 0210 1255 2765' → PRED='622202101255 2765'
> - near_miss [银行卡号] GT='6222 0210 1286 2858' → PRED='6222 021012862858'
> - near_miss [银行卡号] GT='6222 0210 1317 2951' → PRED='6222 021013172951'
> - near_miss [银行卡号] GT='6222 0210 1348 3044' → PRED='6222021013483044'
> - near_miss [银行卡号] GT='6222 0210 1379 3137' → PRED='6222 021013793137'
> - near_miss [银行卡号] GT='6222 0210 1410 3230' → PRED='6222021014103230'
> - near_miss [银行卡号] GT='6222 0210 1441 3323' → PRED='6222021014413323'
> - near_miss [银行卡号] GT='6222 0210 1472 3416' → PRED='6222 02101472 3416'
> - near_miss [银行卡号] GT='6222 0210 1503 3509' → PRED='6222 02101503 3509'
> - near_miss [银行卡号] GT='6222 0210 1534 3602' → PRED='622202101534 3602'
> - near_miss [银行卡号] GT='6222 0210 1565 3695' → PRED='6222 02101565 3695'
> - near_miss [银行卡号] GT='6222 0210 1596 3788' → PRED='6222 02101596 3788'
> - miss [银行卡号] '6222 0210 3021 6063'
> - miss [银行卡号] '6222 0210 3052 6156'
> - miss [银行卡号] '6222 0210 3083 6249'
> - miss [银行卡号] '6222 0210 3114 6342'
> - miss [银行卡号] '6222 0210 3145 6435'
> - miss [银行卡号] '6222 0210 3176 6528'
> - miss [银行卡号] '6222 0210 3207 6621'
> - miss [银行卡号] '6222 0210 3238 6714'
> - miss [银行卡号] '6222 0210 3269 6807'
> - miss [银行卡号] '6222 0210 3300 6900'
> - miss [银行卡号] '6222 0210 1028 2084'
> - miss [银行卡号] '6222 0210 1091 8273'
> - miss [银行卡号] '6222 0210 1154 6462'
> - miss [银行卡号] '6222 0210 2035 8105'
> - miss [银行卡号] '6222 0210 2098 6294'
> - miss [银行卡号] '6222 0210 2161 4483'
> - miss [银行卡号] '6222 0210 3042 6126'
> - miss [银行卡号] '6222 0210 3105 4315'
> - miss [银行卡号] '6222 0210 4049 4147'
> - miss [银行卡号] '6222 0210 4112 2336'
> - near_miss [邮箱] GT='user69.chen@example-corp4.cn' → PRED='user69.chen@example-cor p4.cn'
> - near_miss [邮箱] GT='user55.chen@example-corp0.cn' → PRED='user55.chen@example-cor p0.cn'
> - miss [邮箱] 'user07.chen@example-corp2.cn'
> - miss [邮箱] 'user00.chen@example-corp0.cn'
> - miss [邮箱] 'user24.chen@example-corp4.cn'
> - miss [邮箱] 'user31.chen@example-corp1.cn'
> - miss [邮箱] 'user38.chen@example-corp3.cn'
> - miss [邮箱] 'user86.chen@example-corp1.cn'
> - miss [邮箱] 'user93.chen@example-corp3.cn'
> - miss [邮箱] 'user02.chen@example-corp2.cn'
> - miss [邮箱] 'user03.chen@example-corp3.cn'
> - miss [邮箱] 'user09.chen@example-corp4.cn'
> - miss [邮箱] 'user10.chen@example-corp0.cn'
> - miss [邮箱] 'user16.chen@example-corp1.cn'
> - miss [邮箱] 'user17.chen@example-corp2.cn'
> - miss [邮箱] 'user23.chen@example-corp3.cn'
> - miss [邮箱] 'user26.chen@example-corp1.cn'
> - miss [邮箱] 'user30.chen@example-corp0.cn'
> - miss [邮箱] 'user33.chen@example-corp3.cn'
> - miss [邮箱] 'user37.chen@example-corp2.cn'
> - miss [邮箱] 'user40.chen@example-corp0.cn'
> - miss [邮箱] 'user44.chen@example-corp4.cn'

> [!example]- 明细：速度分解（duration_ms 埋点）
>
> | 文件 | 阶段 | mean ms | p95 ms |
> |---|---|---|---|
> | syn_contract_10p_mid | ocr_has | 13186.0 | 14839.8 |
> | syn_contract_10p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_contract_10p_mid | pdf_render_ms | 84.0 | 85.6 |
> | syn_contract_10p_mid | request_total_ms | 13224.3 | 14877.8 |
> | syn_contract_10p_mid | total | 13208.0 | 14863.6 |
> | syn_contract_10p_mid | visual_features | 6612.8 | 7295.8 |
> | syn_contract_50p_mid | ocr_has | 13104.5 | 14730.6 |
> | syn_contract_50p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_contract_50p_mid | pdf_render_ms | 84.6 | 87.6 |
> | syn_contract_50p_mid | request_total_ms | 13149.1 | 14770.2 |
> | syn_contract_50p_mid | total | 13125.9 | 14751.6 |
> | syn_contract_50p_mid | visual_features | 6479.8 | 7227.0 |
> | syn_judgment_3p_mid | ocr_has | 15244.5 | 15749.0 |
> | syn_judgment_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_judgment_3p_mid | pdf_render_ms | 85.0 | 88.6 |
> | syn_judgment_3p_mid | request_total_ms | 15281.0 | 15784.1 |
> | syn_judgment_3p_mid | total | 15265.5 | 15770.0 |
> | syn_judgment_3p_mid | visual_features | 6462.5 | 6468.4 |
> | syn_contract_10p_dense | ocr_has | 25415.7 | 27077.0 |
> | syn_contract_10p_dense | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_contract_10p_dense | pdf_render_ms | 94.2 | 96.0 |
> | syn_contract_10p_dense | request_total_ms | 25454.2 | 27113.8 |
> | syn_contract_10p_dense | total | 25437.3 | 27098.6 |
> | syn_contract_10p_dense | visual_features | 6473.6 | 6873.0 |
> | syn_contract_10p_sparse | ocr_has | 4667.1 | 4726.8 |
> | syn_contract_10p_sparse | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_contract_10p_sparse | pdf_render_ms | 77.7 | 81.6 |
> | syn_contract_10p_sparse | request_total_ms | 6325.4 | 6773.2 |
> | syn_contract_10p_sparse | total | 6309.7 | 6755.6 |
> | syn_contract_10p_sparse | visual_features | 6287.4 | 6731.8 |
> | syn_contract_3p_mid | ocr_has | 12697.0 | 13224.4 |
> | syn_contract_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_contract_3p_mid | pdf_render_ms | 61.5 | 62.0 |
> | syn_contract_3p_mid | request_total_ms | 12735.0 | 13260.6 |
> | syn_contract_3p_mid | total | 12719.5 | 13246.5 |
> | syn_contract_3p_mid | visual_features | 7024.5 | 7549.6 |
> | syn_judgment_txt_3p_mid | ocr_has | 15181.0 | 15708.4 |
> | syn_judgment_txt_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_judgment_txt_3p_mid | pdf_render_ms | 61.5 | 62.9 |
> | syn_judgment_txt_3p_mid | request_total_ms | 15220.5 | 15743.9 |
> | syn_judgment_txt_3p_mid | total | 15202.0 | 15729.4 |
> | syn_judgment_txt_3p_mid | visual_features | 6370.5 | 6499.6 |
> | syn_hybrid_4p_mid | ocr_has | 13356.7 | 14088.1 |
> | syn_hybrid_4p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_hybrid_4p_mid | pdf_render_ms | 77.3 | 84.0 |
> | syn_hybrid_4p_mid | request_total_ms | 13392.7 | 14123.5 |
> | syn_hybrid_4p_mid | total | 13377.7 | 14109.1 |
> | syn_hybrid_4p_mid | visual_features | 6233.0 | 6436.8 |
> | syn_edge_3p_mixed | ocr_has | 16087.5 | 19961.5 |
> | syn_edge_3p_mixed | pdf_render_cache_hit | 0.0 | 0.0 |
> | syn_edge_3p_mixed | pdf_render_ms | 85.0 | 85.0 |
> | syn_edge_3p_mixed | request_total_ms | 16127.0 | 20000.6 |
> | syn_edge_3p_mixed | total | 16109.0 | 19983.5 |
> | syn_edge_3p_mixed | visual_features | 6114.0 | 6171.6 |

> [!question]- 指标字典（每个指标是什么意思）
>
> | 指标 | 定义与用途 |
> |---|---|
> | 数字保真 exact 率 | 身份证/护照/电话/银行卡四类数字被逐字符正确识别并定位的比例；错一位=该脱的没脱干净=漏脱敏红线。目标恒为 100%（一票否决） |
> | 实体召回 R | 应被识别的敏感实体中实际被找出的比例；漏检=漏脱敏。参考线=NER 引擎层基线 −1pp（LLM NER 实验的三闸门之一） |
> | 实体精确 P | 识别出的实体中真正是敏感实体的比例；误检=把无关内容也脱掉，损害文档可读性 |
> | F1 | P 与 R 的调和平均，效果的综合分 |
> | 宽松口径 wrong_type | 找对了文本但类型标错的条数（如把人名标成机构）；区分「类型分错」与「真漏检」 |
> | near_miss | 数字识别结果与真值仅差空格/连字符/大小写的条数；属 OCR 层噪声，可自动修复修复后转为 exact——是定位「损失在哪一层」的关键信号 |
> | miss | 数字识别结果与真值实质不同的条数；真损失，需要按层排查（OCR/NER/匹配） |
> | 单页耗时 p50 / p95 | 稳态下单页端到端处理时间的中位数与 95 分位；p95 决定用户「最惨等多久」 |
> | 吞吐（页/分钟） | 稳态页均耗时的倒数换算；估算「N 页卷宗要等多久」用总页数÷吞吐 |
> | duration_ms 分解 | 单页内 OCR / NER / 视觉定位(LA) / 匹配各阶段耗时埋点；性能优化的靶子定位 |
> | 失败文件 / 空框页 | 识别请求报错的文件数 / 未检出任何框的页数；稳健性指标，静默失败（job 看着成功但页没处理）是最危险形态 |
> | e2e−ner 差值 | 同一实体串口径下端到端与 NER 引擎层的效果差；差值即 OCR/路由链路引入的损失 |
