# 端到端评测（Issue #37）：preview2.0.0

- 环境标签：**scnet-main-k100**（跨环境不比绝对值）
- 目标：preview2.0.0（http://127.0.0.1:8000）
- 时间：2026-09-13T23:54:40，git：unknown

## 汇总（全部文件 micro 聚合）

- P=0.8419 R=0.8553 F1=0.8486（tp 1342 / fp 252 / fn 227）
- 数字保真闸门：❌ FAIL

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
  - miss: '110102196102226774'
  - miss: '110133199209258248'
  - miss: '110164197804289714'
| 护照号 | 35 | 9 | 5 | 0.7143 |
  - near_miss: GT='E11773856' PRED='E11 773856'
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
  - near_miss: GT='15102175741' PRED='1 5102 1 7574 1'
  - near_miss: GT='13823875918' PRED='1 3823875918'
  - near_miss: GT='16554601165' PRED='1 6 55 4 601 165'
  - near_miss: GT='15102175741' PRED='1 5102 1 7574 1'
  - near_miss: GT='15102175741' PRED='1 5102 1 7574 1'
  - near_miss: GT='13823875918' PRED='1 3823875918'
  - near_miss: GT='16554601165' PRED='1 6 55 4 601 165'
  - near_miss: GT='15102175741' PRED='1 5102 1 7574 1'
  - miss: '14470564194'
  - miss: '16562739935'
  - miss: '19378388453'
  - miss: '13492321604'
  - miss: '13747091417'
  - miss: '14115794381'
  - miss: '14200717652'
  - miss: '14385640923'
  - miss: '14470564194'
  - miss: '14861024568'
  - miss: '15584497345'
  - miss: '15839267158'
  - miss: '16038123580'
  - miss: '16123046851'
  - miss: '16207970122'
  - miss: '16562739935'
  - miss: '17676673086'
  - miss: '17931442899'
  - miss: '18300145863'
  - miss: '18654915676'
  - miss: '19023618640'
  - miss: '19378388453'
  - miss: '19768848827'
  - miss: '13030081100'
  - miss: '13115004371'
  - miss: '13123143141'
  - miss: '13208066412'
  - miss: '14019597400'
  - miss: '14470564194'
  - miss: '14668630606'
  - miss: '14761692647'
  - miss: '15300242153'
  - miss: '16111773141'
  - miss: '16296696412'
  - miss: '16562739935'
  - miss: '16853868388'
  - miss: '16938791659'
  - miss: '17484279124'
  - miss: '17492417894'
  - miss: '17835245918'
  - miss: '18030967400'
  - miss: '19378388453'
  - miss: '19576454865'
  - miss: '19378388453'
  - miss: '19378388453'
  - miss: '19378388453'
| 银行卡号 | 5 | 87 | 42 | 0.0373 |
  - near_miss: GT='6222 0210 1007 2021' PRED='6222 021010072021'
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
  - near_miss: GT='6222 0210 2014 8042' PRED='6222021020148042'
  - near_miss: GT='6222 0210 2045 8135' PRED='6222 021020458135'
  - near_miss: GT='6222 0210 2107 8321' PRED='6222 021021078321'
  - near_miss: GT='6222 0210 2138 8414' PRED='6222021021388414'
  - near_miss: GT='6222 0210 2169 8507' PRED='62220210 2169 8507'
  - near_miss: GT='6222 0210 2200 8600' PRED='6222 021022008600'
  - near_miss: GT='6222 0210 2231 8693' PRED='6222 021022318693'
  - near_miss: GT='6222 0210 2262 8786' PRED='6222 021022628786'
  - near_miss: GT='6222 0210 2293 8879' PRED='6222 02102293 8879'
  - near_miss: GT='6222 0210 1007 2021' PRED='6222 021010072021'
  - near_miss: GT='6222 0210 1038 2114' PRED='6222 021010382114'
  - near_miss: GT='6222 0210 1069 2207' PRED='6222021010692207'
  - near_miss: GT='6222 0210 1007 2021' PRED='6222 021010072021'
  - near_miss: GT='6222 0210 1038 2114' PRED='6222 021010382114'
  - near_miss: GT='6222 0210 1069 2207' PRED='6222021010692207'
  - near_miss: GT='6222 0210 1100 2300' PRED='6222 021011002300'
  - near_miss: GT='6222 0210 1069 2207' PRED='6222021010692207'
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
  - miss: '6222 0210 5056 2168'
  - miss: '6222 0210 5119 8357'
  - miss: '6222 0210 6063 8189'
  - miss: '6222 0210 6126 6378'
  - miss: '6222 0210 7070 6210'
  - miss: '6222 0210 7133 4399'
  - miss: '6222 0210 8077 4231'
  - miss: '6222 0210 8140 2420'
  - miss: '6222 0210 9084 2252'
  - miss: '6222 0210 9147 8441'
  - miss: '6222 0210 1059 2177'
  - miss: '6222 0210 1122 8366'
  - miss: '6222 0210 2066 8198'
  - miss: '6222 0210 2129 6387'
  - miss: '6222 0210 3073 6219'
  - miss: '6222 0210 3136 4408'
  - miss: '6222 0210 4080 4240'
  - miss: '6222 0210 5087 2261'
  - miss: '6222 0210 6094 8282'
  - miss: '6222 0210 7101 6303'
  - miss: '6222 0210 8108 4324'
  - miss: '6222 0210 9115 2345'
| 邮箱 | 28 | 7 | 53 | 0.3182 |
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - near_miss: GT='user55.chen@example-corp0.cn' PRED='user55.chen@example-cor p0.cn'
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - near_miss: GT='user69.chen@example-corp4.cn' PRED='user69.chen@example-cor p4.cn'
  - miss: 'user07.chen@example-corp2.cn'
  - miss: 'user00.chen@example-corp0.cn'
  - miss: 'user07.chen@example-corp2.cn'
  - miss: 'user24.chen@example-corp4.cn'
  - miss: 'user31.chen@example-corp1.cn'
  - miss: 'user38.chen@example-corp3.cn'
  - miss: 'user86.chen@example-corp1.cn'
  - miss: 'user93.chen@example-corp3.cn'
  - miss: 'user00.chen@example-corp0.cn'
  - miss: 'user02.chen@example-corp2.cn'
  - miss: 'user03.chen@example-corp3.cn'
  - miss: 'user07.chen@example-corp2.cn'
  - miss: 'user09.chen@example-corp4.cn'
  - miss: 'user10.chen@example-corp0.cn'
  - miss: 'user16.chen@example-corp1.cn'
  - miss: 'user17.chen@example-corp2.cn'
  - miss: 'user23.chen@example-corp3.cn'
  - miss: 'user24.chen@example-corp4.cn'
  - miss: 'user26.chen@example-corp1.cn'
  - miss: 'user30.chen@example-corp0.cn'
  - miss: 'user31.chen@example-corp1.cn'
  - miss: 'user33.chen@example-corp3.cn'
  - miss: 'user37.chen@example-corp2.cn'
  - miss: 'user38.chen@example-corp3.cn'
  - miss: 'user40.chen@example-corp0.cn'
  - miss: 'user44.chen@example-corp4.cn'
  - miss: 'user47.chen@example-corp2.cn'
  - miss: 'user51.chen@example-corp1.cn'
  - miss: 'user21.chen@example-corp1.cn'
  - miss: 'user24.chen@example-corp4.cn'
  - miss: 'user31.chen@example-corp1.cn'
  - miss: 'user76.chen@example-corp1.cn'
  - miss: 'user83.chen@example-corp3.cn'
  - miss: 'user86.chen@example-corp1.cn'
  - miss: 'user07.chen@example-corp2.cn'
  - miss: 'user38.chen@example-corp3.cn'
  - miss: 'user00.chen@example-corp0.cn'
  - miss: 'user07.chen@example-corp2.cn'
  - miss: 'user38.chen@example-corp3.cn'

## 分文件

| 文件 | 载体 | GT 实体 | P | R | F1 | 数字闸门 | 总耗时 s | 页/分钟 |
|---|---|---|---|---|---|---|---|---|
| syn_contract_1p_mid | scanned_pdf | 13 | 0.8571 | 0.9231 | 0.8889 | ❌ | 11.055 | 0 |
| syn_contract_10p_mid | scanned_pdf | 135 | 0.8446 | 0.9259 | 0.8834 | ❌ | 132.524 | 4.5 |
| syn_contract_50p_mid | scanned_pdf | 675 | 0.8378 | 0.9185 | 0.8763 | ❌ | 748.914 | 3.988 |
| syn_judgment_3p_mid | scanned_pdf | 30 | 0.7500 | 0.8000 | 0.7742 | ❌ | 46.778 | 3.906 |
| syn_contract_10p_dense | scanned_pdf | 395 | 0.8426 | 0.7316 | 0.7832 | ❌ | 254.415 | 2.341 |
| syn_contract_10p_sparse | scanned_pdf | 20 | 1.0000 | 1.0000 | 1.0000 | ✅ | 61.808 | 9.697 |
| syn_contract_3p_mid | text_pdf | 40 | 0.8222 | 0.9250 | 0.8706 | ❌ | 37.382 | 4.713 |
| syn_judgment_3p_mid | text_pdf | 30 | 0.7500 | 0.8000 | 0.7742 | ❌ | 46.811 | 3.922 |
| syn_warrant_1p_mid | text_pdf | 7 | 0.5714 | 0.5714 | 0.5714 | ✅ | 8.677 | 0 |
| syn_hybrid_4p_mid | hybrid_pdf | 54 | 0.8065 | 0.9259 | 0.8621 | ❌ | 52.348 | 4.485 |
| syn_contract_2p_mid | docx | 27 | 1.0000 | 1.0000 | 1.0000 | ✅ | 13.144 | 4.565 |
| syn_judgment_2p_mid | docx | 20 | 1.0000 | 1.0000 | 1.0000 | ✅ | 10.088 | 5.948 |
| syn_contract_txt_1p_mid | txt | 13 | 1.0000 | 1.0000 | 1.0000 | ✅ | 6.973 | 8.605 |
| syn_statement_1p_table | txt | 61 | 1.0000 | 0.6721 | 0.8039 | ❌ | 14.031 | 4.276 |
| syn_edge_3p_mixed | scanned_pdf | 49 | 0.7200 | 0.7347 | 0.7273 | ❌ | 38.67 | 3.674 |

## 速度分解（steady 页，duration_ms 埋点）

| 文件 | 段 | mean ms | p95 ms |
|---|---|---|---|
| syn_contract_10p_mid | ocr_has | 13277.7 | 14839.0 |
| syn_contract_10p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_mid | pdf_render_ms | 93.4 | 134.6 |
| syn_contract_10p_mid | request_total_ms | 13314.4 | 14878.2 |
| syn_contract_10p_mid | total | 13298.8 | 14860.0 |
| syn_contract_10p_mid | visual_features | 6098.1 | 6126.8 |
| syn_contract_50p_mid | ocr_has | 14948.4 | 26343.6 |
| syn_contract_50p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_50p_mid | pdf_render_ms | 84.6 | 87.6 |
| syn_contract_50p_mid | request_total_ms | 14998.2 | 26386.6 |
| syn_contract_50p_mid | total | 14969.8 | 26364.6 |
| syn_contract_50p_mid | visual_features | 6248.2 | 7216.4 |
| syn_judgment_3p_mid | ocr_has | 15312.0 | 15934.8 |
| syn_judgment_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_judgment_3p_mid | pdf_render_ms | 59.5 | 60.0 |
| syn_judgment_3p_mid | request_total_ms | 15349.0 | 15973.6 |
| syn_judgment_3p_mid | total | 15334.0 | 15956.8 |
| syn_judgment_3p_mid | visual_features | 6189.5 | 6334.9 |
| syn_contract_10p_dense | ocr_has | 25561.1 | 27337.8 |
| syn_contract_10p_dense | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_dense | pdf_render_ms | 93.4 | 95.2 |
| syn_contract_10p_dense | request_total_ms | 25598.9 | 27374.4 |
| syn_contract_10p_dense | total | 25582.8 | 27359.2 |
| syn_contract_10p_dense | visual_features | 6255.9 | 6325.6 |
| syn_contract_10p_sparse | ocr_has | 4701.3 | 4778.6 |
| syn_contract_10p_sparse | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_10p_sparse | pdf_render_ms | 76.6 | 78.2 |
| syn_contract_10p_sparse | request_total_ms | 6172.3 | 6393.2 |
| syn_contract_10p_sparse | total | 6155.4 | 6375.6 |
| syn_contract_10p_sparse | visual_features | 6133.1 | 6350.4 |
| syn_contract_3p_mid | ocr_has | 12680.5 | 13130.0 |
| syn_contract_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_contract_3p_mid | pdf_render_ms | 63.5 | 64.0 |
| syn_contract_3p_mid | request_total_ms | 12717.0 | 13169.7 |
| syn_contract_3p_mid | total | 12701.5 | 13151.0 |
| syn_contract_3p_mid | visual_features | 6085.5 | 6088.6 |
| syn_judgment_3p_mid | ocr_has | 15251.0 | 15804.5 |
| syn_judgment_3p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_judgment_3p_mid | pdf_render_ms | 60.0 | 60.9 |
| syn_judgment_3p_mid | request_total_ms | 15286.0 | 15838.6 |
| syn_judgment_3p_mid | total | 15272.0 | 15825.5 |
| syn_judgment_3p_mid | visual_features | 6195.5 | 6230.1 |
| syn_hybrid_4p_mid | ocr_has | 13324.3 | 14094.6 |
| syn_hybrid_4p_mid | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_hybrid_4p_mid | pdf_render_ms | 76.7 | 84.9 |
| syn_hybrid_4p_mid | request_total_ms | 13361.7 | 14135.0 |
| syn_hybrid_4p_mid | total | 13346.0 | 14115.6 |
| syn_hybrid_4p_mid | visual_features | 6127.3 | 6162.1 |
| syn_edge_3p_mixed | ocr_has | 16282.5 | 20283.4 |
| syn_edge_3p_mixed | pdf_render_cache_hit | 0.0 | 0.0 |
| syn_edge_3p_mixed | pdf_render_ms | 84.5 | 85.0 |
| syn_edge_3p_mixed | request_total_ms | 16317.0 | 20317.5 |
| syn_edge_3p_mixed | total | 16304.0 | 20304.5 |
| syn_edge_3p_mixed | visual_features | 6181.0 | 6281.8 |
