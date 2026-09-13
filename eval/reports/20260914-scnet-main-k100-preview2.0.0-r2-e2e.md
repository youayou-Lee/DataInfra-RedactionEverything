# 端到端评测（Issue #37）：preview2.0.0-r2

- 环境标签：**scnet-main-k100**（跨环境不比绝对值）
- 目标：preview2.0.0-r2（http://127.0.0.1:8000）
- 时间：2026-09-14T00:33:44，git：unknown

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
| 身份证号 | 77 | 0 | 1 | 0.9872 |
  - miss: '110164197804289714'
| 护照号 | 24 | 6 | 5 | 0.6857 |
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - miss: 'E78791789'
  - miss: 'E79282767'
  - miss: 'E79773745'
  - miss: 'E80264723'
  - miss: 'E80755701'
| 电话 | 106 | 0 | 37 | 0.7413 |
  - miss: '13030081100'
  - miss: '13115004371'
  - miss: '13123143141'
  - miss: '13208066412'
  - miss: '13492321604'
  - miss: '13747091417'
  - miss: '14019597400'
  - miss: '14115794381'
  - miss: '14200717652'
  - miss: '14385640923'
  - miss: '14668630606'
  - miss: '14761692647'
  - miss: '14861024568'
  - miss: '15300242153'
  - miss: '15584497345'
  - miss: '15839267158'
  - miss: '16038123580'
  - miss: '16111773141'
  - miss: '16123046851'
  - miss: '16207970122'
| 银行卡号 | 4 | 56 | 42 | 0.0392 |
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - near_miss: GT='gt' PRED='pred'
  - miss: '6222 0210 1028 2084'
  - miss: '6222 0210 1059 2177'
  - miss: '6222 0210 1091 8273'
  - miss: '6222 0210 1122 8366'
  - miss: '6222 0210 1154 6462'
  - miss: '6222 0210 2035 8105'
  - miss: '6222 0210 2066 8198'
  - miss: '6222 0210 2098 6294'
  - miss: '6222 0210 2129 6387'
  - miss: '6222 0210 2161 4483'
  - miss: '6222 0210 3021 6063'
  - miss: '6222 0210 3042 6126'
  - miss: '6222 0210 3052 6156'
  - miss: '6222 0210 3073 6219'
  - miss: '6222 0210 3083 6249'
  - miss: '6222 0210 3105 4315'
  - miss: '6222 0210 3114 6342'
  - miss: '6222 0210 3136 4408'
  - miss: '6222 0210 3145 6435'
  - miss: '6222 0210 3176 6528'
| 邮箱 | 22 | 1 | 33 | 0.3929 |
  - near_miss: GT='gt' PRED='pred'
  - miss: 'user02.chen@example-corp2.cn'
  - miss: 'user03.chen@example-corp3.cn'
  - miss: 'user09.chen@example-corp4.cn'
  - miss: 'user10.chen@example-corp0.cn'
  - miss: 'user16.chen@example-corp1.cn'
  - miss: 'user17.chen@example-corp2.cn'
  - miss: 'user21.chen@example-corp1.cn'
  - miss: 'user23.chen@example-corp3.cn'
  - miss: 'user24.chen@example-corp4.cn'
  - miss: 'user26.chen@example-corp1.cn'
  - miss: 'user30.chen@example-corp0.cn'
  - miss: 'user31.chen@example-corp1.cn'
  - miss: 'user33.chen@example-corp3.cn'
  - miss: 'user37.chen@example-corp2.cn'
  - miss: 'user40.chen@example-corp0.cn'
  - miss: 'user44.chen@example-corp4.cn'
  - miss: 'user47.chen@example-corp2.cn'
  - miss: 'user51.chen@example-corp1.cn'
  - miss: 'user54.chen@example-corp4.cn'
  - miss: 'user58.chen@example-corp3.cn'

## 分文件

| 文件 | 载体 | GT 实体 | P | R | F1 | 数字闸门 | 总耗时 s | 页/分钟 |
|---|---|---|---|---|---|---|---|---|
| syn_contract_1p_mid | scanned_pdf | 13 | 0.8571 | 0.9231 | 0.8889 | ❌ | 13.597 | n/a |
| syn_contract_10p_mid | scanned_pdf | 135 | 0.8446 | 0.9259 | 0.8834 | ❌ | 131.386 | 4.526 |
| syn_contract_50p_mid | scanned_pdf | 675 | 0.8378 | 0.9185 | 0.8763 | ❌ | 660.665 | 4.53 |
| syn_judgment_3p_mid | scanned_pdf | 30 | 0.7500 | 0.8000 | 0.7742 | ❌ | 46.728 | 3.914 |
| syn_contract_10p_dense | scanned_pdf | 395 | 0.8426 | 0.7316 | 0.7832 | ❌ | 253.916 | 2.347 |
| syn_contract_10p_sparse | scanned_pdf | 20 | 1.0000 | 1.0000 | 1.0000 | ✅ | 62.488 | 9.591 |
| syn_contract_3p_mid | text_pdf | 40 | 0.8222 | 0.9250 | 0.8706 | ❌ | 38.241 | 4.632 |
| syn_judgment_txt_3p_mid | text_pdf | 30 | 0.7500 | 0.8000 | 0.7742 | ❌ | 46.981 | 3.907 |
| syn_warrant_1p_mid | text_pdf | 7 | 0.5714 | 0.5714 | 0.5714 | ✅ | 8.589 | n/a |
| syn_hybrid_4p_mid | hybrid_pdf | 54 | 0.8065 | 0.9259 | 0.8621 | ❌ | 52.052 | 4.497 |
| syn_contract_2p_mid | docx | 27 | 1.0000 | 1.0000 | 1.0000 | ✅ | 13.041 | 4.601 |
| syn_judgment_2p_mid | docx | 20 | 1.0000 | 1.0000 | 1.0000 | ✅ | 10.274 | 5.84 |
| syn_contract_txt_1p_mid | txt | 13 | 1.0000 | 1.0000 | 1.0000 | ✅ | 6.992 | 8.581 |
| syn_statement_1p_table | txt | 61 | 1.0000 | 0.6721 | 0.8039 | ❌ | 13.837 | 4.336 |
| syn_edge_3p_mixed | scanned_pdf | 49 | 0.7200 | 0.7347 | 0.7273 | ❌ | 38.051 | 3.75 |

## 速度分解（steady 页，duration_ms 埋点）

| 文件 | 段 | mean ms | p95 ms |
|---|---|---|---|
| syn_contract_10p_mid | ocr_has | 13198.8 | 14749.2 |
| syn_contract_10p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_mid | pdf_render_ms | 85.6 | 94.8 |
| syn_contract_10p_mid | request_total_ms | 13237.0 | 14785.2 |
| syn_contract_10p_mid | total | 13220.7 | 14770.2 |
| syn_contract_10p_mid | visual_features | 6101.8 | 6164.8 |
| syn_contract_50p_mid | ocr_has | 13145.9 | 15002.4 |
| syn_contract_50p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_50p_mid | pdf_render_ms | 87.6 | 93.2 |
| syn_contract_50p_mid | request_total_ms | 13192.2 | 15048.4 |
| syn_contract_50p_mid | total | 13167.5 | 15025.0 |
| syn_contract_50p_mid | visual_features | 6115.1 | 6244.0 |
| syn_judgment_3p_mid | ocr_has | 15282.0 | 15904.8 |
| syn_judgment_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_judgment_3p_mid | pdf_render_ms | 82.0 | 82.9 |
| syn_judgment_3p_mid | request_total_ms | 15320.0 | 15940.1 |
| syn_judgment_3p_mid | total | 15303.5 | 15926.8 |
| syn_judgment_3p_mid | visual_features | 6229.0 | 6323.5 |
| syn_contract_10p_dense | ocr_has | 25488.6 | 27121.2 |
| syn_contract_10p_dense | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_dense | pdf_render_ms | 96.3 | 102.8 |
| syn_contract_10p_dense | request_total_ms | 25531.0 | 27157.8 |
| syn_contract_10p_dense | total | 25511.0 | 27142.6 |
| syn_contract_10p_dense | visual_features | 6291.8 | 6391.4 |
| syn_contract_10p_sparse | ocr_has | 4804.4 | 4885.0 |
| syn_contract_10p_sparse | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_sparse | pdf_render_ms | 77.7 | 85.4 |
| syn_contract_10p_sparse | request_total_ms | 6242.1 | 6484.2 |
| syn_contract_10p_sparse | total | 6224.7 | 6470.6 |
| syn_contract_10p_sparse | visual_features | 6203.2 | 6448.4 |
| syn_contract_3p_mid | ocr_has | 12900.0 | 13431.0 |
| syn_contract_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_3p_mid | pdf_render_ms | 62.5 | 63.9 |
| syn_contract_3p_mid | request_total_ms | 12938.0 | 13468.1 |
| syn_contract_3p_mid | total | 12921.0 | 13452.0 |
| syn_contract_3p_mid | visual_features | 6999.5 | 7170.1 |
| syn_judgment_txt_3p_mid | ocr_has | 15309.5 | 15854.5 |
| syn_judgment_txt_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_judgment_txt_3p_mid | pdf_render_ms | 61.0 | 62.8 |
| syn_judgment_txt_3p_mid | request_total_ms | 15345.5 | 15887.8 |
| syn_judgment_txt_3p_mid | total | 15330.5 | 15875.5 |
| syn_judgment_txt_3p_mid | visual_features | 6161.5 | 6179.9 |
| syn_hybrid_4p_mid | ocr_has | 13290.0 | 13962.8 |
| syn_hybrid_4p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_hybrid_4p_mid | pdf_render_ms | 80.7 | 96.6 |
| syn_hybrid_4p_mid | request_total_ms | 13326.3 | 13999.9 |
| syn_hybrid_4p_mid | total | 13311.3 | 13984.7 |
| syn_hybrid_4p_mid | visual_features | 6446.3 | 6589.8 |
| syn_edge_3p_mixed | ocr_has | 15948.0 | 19949.4 |
| syn_edge_3p_mixed | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_edge_3p_mixed | pdf_render_ms | 85.0 | 85.0 |
| syn_edge_3p_mixed | request_total_ms | 15983.0 | 19984.4 |
| syn_edge_3p_mixed | total | 15969.5 | 19971.3 |
| syn_edge_3p_mixed | visual_features | 6448.5 | 6835.1 |
