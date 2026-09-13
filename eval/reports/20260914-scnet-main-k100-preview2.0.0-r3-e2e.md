# 端到端评测（Issue #37）：preview2.0.0-r3

- 环境标签：**scnet-main-k100**（跨环境不比绝对值）
- 目标：preview2.0.0-r3（http://127.0.0.1:8000）
- 时间：2026-09-14T01:24:36，git：unknown

## 汇总（全部文件 micro 聚合）

- P=0.8419 R=0.8553 F1=0.8486（tp 1342 / fp 252 / fn 227）
- 数字保真闸门：❌ FAIL
- 宽松口径（span 对、类型错）：0 条

| 类型 | P | R | F1 | tp | fp | fn |
|---|---|---|---|---|---|---|
| 地址 | 0.7712 | 0.8198 | 0.7948 | 91 | 27 | 20 |
| 姓名 | 1.0000 | 0.9733 | 0.9865 | 365 | 0 | 10 |
| 护照号 | 1.0000 | 0.8980 | 0.9462 | 44 | 0 | 5 |
| 日期 | 0.9789 | 0.9134 | 0.9450 | 232 | 5 | 22 |
| 机构名称 | 0.6125 | 0.9703 | 0.7510 | 196 | 124 | 6 |
| 电话 | 0.9886 | 0.7793 | 0.8715 | 173 | 2 | 49 |
| 身份证号 | 0.9661 | 0.9500 | 0.9580 | 114 | 4 | 6 |
| 邮箱 | 0.3097 | 0.3431 | 0.3256 | 35 | 78 | 67 |
| 银行卡号 | 0.8846 | 0.6866 | 0.7731 | 92 | 12 | 42 |

## 数字实体逐字保真（一票否决区）

| 类型 | exact | near_miss | miss | exact_rate |
|---|---|---|---|---|
| 身份证号 | 114 | 0 | 6 | 0.9500 |
  - miss: '110102196102226774'
  - miss: '110133199209258248'
  - miss: '110164197804289714'
| 护照号 | 35 | 9 | 5 | 0.7143 |
  - near_miss: GT='E11773856' PRED='E11 773856'
  - near_miss: GT='E13737768' PRED='E1373 7768'
  - near_miss: GT='E17174614' PRED='E 171 74614'
  - near_miss: GT='E21593416' PRED='E21 593416'
  - near_miss: GT='E11282878' PRED='E1 1 282878'
  - near_miss: GT='E11773856' PRED='E 1 1 7 73856'
  - near_miss: GT='E90773800' PRED='E 90773800'
  - near_miss: GT='E91264778' PRED='E 91 264 778'
  - miss: 'E78791789'
  - miss: 'E79282767'
  - miss: 'E79773745'
  - miss: 'E80264723'
  - miss: 'E80755701'
| 电话 | 164 | 9 | 49 | 0.7387 |
  - near_miss: GT='15102175741' PRED='1 5102 1 7574 1'
  - near_miss: GT='13823875918' PRED='1 3823875918'
  - near_miss: GT='16554601165' PRED='1 6 55 4 601 165'
  - miss: '14470564194'
  - miss: '16562739935'
  - miss: '19378388453'
  - miss: '13492321604'
  - miss: '13747091417'
  - miss: '14115794381'
  - miss: '14200717652'
  - miss: '14385640923'
  - miss: '14861024568'
  - miss: '15584497345'
  - miss: '15839267158'
  - miss: '16038123580'
  - miss: '16123046851'
  - miss: '16207970122'
  - miss: '17676673086'
  - miss: '17931442899'
  - miss: '18300145863'
  - miss: '18654915676'
  - miss: '19023618640'
  - miss: '19768848827'
| 银行卡号 | 5 | 87 | 42 | 0.0373 |
  - near_miss: GT='6222 0210 1007 2021' PRED='6222 021010072021'
  - near_miss: GT='6222 0210 1038 2114' PRED='6222 021010382114'
  - near_miss: GT='6222 0210 1069 2207' PRED='6222021010692207'
  - near_miss: GT='6222 0210 1100 2300' PRED='6222 021011002300'
  - near_miss: GT='6222 0210 1131 2393' PRED='6222 021011312393'
  - near_miss: GT='6222 0210 1162 2486' PRED='622202101162 2486'
  - near_miss: GT='6222 0210 1193 2579' PRED='6222 021011932579'
  - near_miss: GT='6222 0210 1224 2672' PRED='6222021012242672'
  - near_miss: GT='6222 0210 1255 2765' PRED='622202101255 2765'
  - near_miss: GT='6222 0210 1286 2858' PRED='6222 021012862858'
  - near_miss: GT='6222 0210 1317 2951' PRED='6222 021013172951'
  - near_miss: GT='6222 0210 1348 3044' PRED='6222021013483044'
  - near_miss: GT='6222 0210 1379 3137' PRED='6222 021013793137'
  - near_miss: GT='6222 0210 1410 3230' PRED='6222021014103230'
  - near_miss: GT='6222 0210 1441 3323' PRED='6222021014413323'
  - near_miss: GT='6222 0210 1472 3416' PRED='6222 02101472 3416'
  - near_miss: GT='6222 0210 1503 3509' PRED='6222 02101503 3509'
  - near_miss: GT='6222 0210 1534 3602' PRED='622202101534 3602'
  - near_miss: GT='6222 0210 1565 3695' PRED='6222 02101565 3695'
  - near_miss: GT='6222 0210 1596 3788' PRED='6222 02101596 3788'
  - miss: '6222 0210 3021 6063'
  - miss: '6222 0210 3052 6156'
  - miss: '6222 0210 3083 6249'
  - miss: '6222 0210 3114 6342'
  - miss: '6222 0210 3145 6435'
  - miss: '6222 0210 3176 6528'
  - miss: '6222 0210 3207 6621'
  - miss: '6222 0210 3238 6714'
  - miss: '6222 0210 3269 6807'
  - miss: '6222 0210 3300 6900'
  - miss: '6222 0210 1028 2084'
  - miss: '6222 0210 1091 8273'
  - miss: '6222 0210 1154 6462'
  - miss: '6222 0210 2035 8105'
  - miss: '6222 0210 2098 6294'
  - miss: '6222 0210 2161 4483'
  - miss: '6222 0210 3042 6126'
  - miss: '6222 0210 3105 4315'
  - miss: '6222 0210 4049 4147'
  - miss: '6222 0210 4112 2336'
| 邮箱 | 28 | 7 | 53 | 0.3182 |
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - near_miss: GT='user55.chen@example-corp0.cn' PRED='user55.chen@example-cor p0.cn'
  - miss: 'user07.chen@example-corp2.cn'
  - miss: 'user00.chen@example-corp0.cn'
  - miss: 'user24.chen@example-corp4.cn'
  - miss: 'user31.chen@example-corp1.cn'
  - miss: 'user38.chen@example-corp3.cn'
  - miss: 'user86.chen@example-corp1.cn'
  - miss: 'user93.chen@example-corp3.cn'
  - miss: 'user02.chen@example-corp2.cn'
  - miss: 'user03.chen@example-corp3.cn'
  - miss: 'user09.chen@example-corp4.cn'
  - miss: 'user10.chen@example-corp0.cn'
  - miss: 'user16.chen@example-corp1.cn'
  - miss: 'user17.chen@example-corp2.cn'
  - miss: 'user23.chen@example-corp3.cn'
  - miss: 'user26.chen@example-corp1.cn'
  - miss: 'user30.chen@example-corp0.cn'
  - miss: 'user33.chen@example-corp3.cn'
  - miss: 'user37.chen@example-corp2.cn'
  - miss: 'user40.chen@example-corp0.cn'
  - miss: 'user44.chen@example-corp4.cn'

## 分文件

| 文件 | 载体 | GT 实体 | P | R | F1 | 数字闸门 | 总耗时 s | 页/分钟 |
|---|---|---|---|---|---|---|---|---|
| syn_contract_1p_mid | scanned_pdf | 13 | 0.8571 | 0.9231 | 0.8889 | ❌ | 15.528 | n/a |
| syn_contract_10p_mid | scanned_pdf | 135 | 0.8446 | 0.9259 | 0.8834 | ❌ | 131.288 | 4.53 |
| syn_contract_50p_mid | scanned_pdf | 675 | 0.8378 | 0.9185 | 0.8763 | ❌ | 658.952 | 4.545 |
| syn_judgment_3p_mid | scanned_pdf | 30 | 0.7500 | 0.8000 | 0.7742 | ❌ | 46.617 | 3.921 |
| syn_contract_10p_dense | scanned_pdf | 395 | 0.8426 | 0.7316 | 0.7832 | ❌ | 253.274 | 2.355 |
| syn_contract_10p_sparse | scanned_pdf | 20 | 1.0000 | 1.0000 | 1.0000 | ✅ | 63.23 | 9.461 |
| syn_contract_3p_mid | text_pdf | 40 | 0.8222 | 0.9250 | 0.8706 | ❌ | 37.378 | 4.706 |
| syn_judgment_txt_3p_mid | text_pdf | 30 | 0.7500 | 0.8000 | 0.7742 | ❌ | 46.734 | 3.937 |
| syn_warrant_1p_mid | text_pdf | 7 | 0.5714 | 0.5714 | 0.5714 | ✅ | 8.565 | n/a |
| syn_hybrid_4p_mid | hybrid_pdf | 54 | 0.8065 | 0.9259 | 0.8621 | ❌ | 52.181 | 4.474 |
| syn_contract_2p_mid | docx | 27 | 1.0000 | 1.0000 | 1.0000 | ✅ | 12.898 | 4.652 |
| syn_judgment_2p_mid | docx | 20 | 1.0000 | 1.0000 | 1.0000 | ✅ | 10.095 | 5.944 |
| syn_contract_txt_1p_mid | txt | 13 | 1.0000 | 1.0000 | 1.0000 | ✅ | 6.846 | 8.764 |
| syn_statement_1p_table | txt | 61 | 1.0000 | 0.6721 | 0.8039 | ❌ | 13.904 | 4.315 |
| syn_edge_3p_mixed | scanned_pdf | 49 | 0.7200 | 0.7347 | 0.7273 | ❌ | 38.314 | 3.717 |

## 速度分解（steady 页，duration_ms 埋点）

| 文件 | 段 | mean ms | p95 ms |
|---|---|---|---|
| syn_contract_10p_mid | ocr_has | 13186.0 | 14839.8 |
| syn_contract_10p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_mid | pdf_render_ms | 84.0 | 85.6 |
| syn_contract_10p_mid | request_total_ms | 13224.3 | 14877.8 |
| syn_contract_10p_mid | total | 13208.0 | 14863.6 |
| syn_contract_10p_mid | visual_features | 6612.8 | 7295.8 |
| syn_contract_50p_mid | ocr_has | 13104.5 | 14730.6 |
| syn_contract_50p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_50p_mid | pdf_render_ms | 84.6 | 87.6 |
| syn_contract_50p_mid | request_total_ms | 13149.1 | 14770.2 |
| syn_contract_50p_mid | total | 13125.9 | 14751.6 |
| syn_contract_50p_mid | visual_features | 6479.8 | 7227.0 |
| syn_judgment_3p_mid | ocr_has | 15244.5 | 15749.0 |
| syn_judgment_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_judgment_3p_mid | pdf_render_ms | 85.0 | 88.6 |
| syn_judgment_3p_mid | request_total_ms | 15281.0 | 15784.1 |
| syn_judgment_3p_mid | total | 15265.5 | 15770.0 |
| syn_judgment_3p_mid | visual_features | 6462.5 | 6468.4 |
| syn_contract_10p_dense | ocr_has | 25415.7 | 27077.0 |
| syn_contract_10p_dense | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_dense | pdf_render_ms | 94.2 | 96.0 |
| syn_contract_10p_dense | request_total_ms | 25454.2 | 27113.8 |
| syn_contract_10p_dense | total | 25437.3 | 27098.6 |
| syn_contract_10p_dense | visual_features | 6473.6 | 6873.0 |
| syn_contract_10p_sparse | ocr_has | 4667.1 | 4726.8 |
| syn_contract_10p_sparse | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_sparse | pdf_render_ms | 77.7 | 81.6 |
| syn_contract_10p_sparse | request_total_ms | 6325.4 | 6773.2 |
| syn_contract_10p_sparse | total | 6309.7 | 6755.6 |
| syn_contract_10p_sparse | visual_features | 6287.4 | 6731.8 |
| syn_contract_3p_mid | ocr_has | 12697.0 | 13224.4 |
| syn_contract_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_3p_mid | pdf_render_ms | 61.5 | 62.0 |
| syn_contract_3p_mid | request_total_ms | 12735.0 | 13260.6 |
| syn_contract_3p_mid | total | 12719.5 | 13246.5 |
| syn_contract_3p_mid | visual_features | 7024.5 | 7549.6 |
| syn_judgment_txt_3p_mid | ocr_has | 15181.0 | 15708.4 |
| syn_judgment_txt_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_judgment_txt_3p_mid | pdf_render_ms | 61.5 | 62.9 |
| syn_judgment_txt_3p_mid | request_total_ms | 15220.5 | 15743.9 |
| syn_judgment_txt_3p_mid | total | 15202.0 | 15729.4 |
| syn_judgment_txt_3p_mid | visual_features | 6370.5 | 6499.6 |
| syn_hybrid_4p_mid | ocr_has | 13356.7 | 14088.1 |
| syn_hybrid_4p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_hybrid_4p_mid | pdf_render_ms | 77.3 | 84.0 |
| syn_hybrid_4p_mid | request_total_ms | 13392.7 | 14123.5 |
| syn_hybrid_4p_mid | total | 13377.7 | 14109.1 |
| syn_hybrid_4p_mid | visual_features | 6233.0 | 6436.8 |
| syn_edge_3p_mixed | ocr_has | 16087.5 | 19961.5 |
| syn_edge_3p_mixed | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_edge_3p_mixed | pdf_render_ms | 85.0 | 85.0 |
| syn_edge_3p_mixed | request_total_ms | 16127.0 | 20000.6 |
| syn_edge_3p_mixed | total | 16109.0 | 19983.5 |
| syn_edge_3p_mixed | visual_features | 6114.0 | 6171.6 |
