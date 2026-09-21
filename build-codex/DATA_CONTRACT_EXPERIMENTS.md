# 実データ契約の判断に使う人工データ実験

2026-09-11。Codexの別担当が、Tempの専用DuckDBとfake clientで現行legacy取込を実測した。外部通信・実サービス応答・実審査は使っていない。製品コードや共通仕様を変更する実験ではなく、新しい適格性契約の採用も行っていない。

## 価格と出来高の調整基準

raw OHLCを10/12/9/11、調整OHLCを20/24/18/22、raw出来高を100、調整出来高を50、売買代金を1100、調整係数を2とした人工入力を、4つの独立homeへ取り込んだ。値は実市場の調整関係を表さない。

| 入力の組合せ | 保存OHLC | 保存出来高 | 保存売買代金 | 保存調整係数 |
|---|---|---:|---:|---:|
| 全調整OHLC・調整出来高 | 20/24/18/22 | 50 | 1100 | 2 |
| 調整Close欠損・調整出来高 | 10/12/9/11 | 50 | 1100 | 2 |
| raw OHLC・raw出来高 | 10/12/9/11 | 100 | 1100 | 2 |
| 全調整OHLC・raw出来高 | 20/24/18/22 | 100 | 1100 | 2 |

OHLCは調整4項目が揃うかどうかで一括選択されるが、出来高は独立に選択される。売買代金・係数も入力の値が保存される。部分調整のケースで、raw価格と調整出来高を組み合わせる現行動作を確認した。

4件とも読取診断はINSPECTED、sourceはjquants_legacy_unverified、ready_for_live=falseだった。共通の警告はADJUSTMENT_BASIS_UNVERIFIED、EVENT_INPUT_UNVERIFIED、LOT_SIZE_UNVERIFIED、PUBLICATION_ESTIMATE_HISTORY_UNVERIFIED。INSPECTEDは適格という判定ではなく、各組合せを経済的に検証した意味でもない。

## 推定公表日の過去行と最新フラグ

| 操作 | 保存された信用残の日付・公表日 | publication_estimated |
|---|---|---|
| 1回目：2026-08-03の行、公表日欠損 | 2026-08-03 → 2026-08-07（研究用+4暦日） | True |
| 2回目：2026-08-10の別行、公表日2026-08-11 | 旧行を保持し、2026-08-10 → 2026-08-11を追加 | False |

最新batchのFalseは、保存済み全行に推定日がないことを証明しない。2回目後の読取診断でもPUBLICATION_ESTIMATE_HISTORY_UNVERIFIEDが表示され、ready_for_live=falseが維持された。行単位の由来や取得run履歴へ移行する機能は、この実験では追加していない。

## 単元・イベント欠損の現在の到達先

ここは新しい取込実験ではなく、ソースと既存試験の独立照合である。

| 入力不足 | 現在の伝播 | 保証する範囲 |
|---|---|---|
| lots省略 | packet_cliは100を既定化しsnapshot・Proposalの単元と数量に固定 | 実銘柄の単元確認を保証しない。イベント既知・両承認等の他条件が満たされれば予約へ進み得る |
| events省略 | ProposalにUNKNOWN、審査資料に情報不足の警告、最終ゲートが独立に拒否 | 模擬judgeが承認してもゲートは許可しない。欠損を確認済み情報へ変換しない |
| 日次市場DB不足 | sessionや当日価格等の事前検査でDATA_INCOMPLETE | 汎用packetのlots/events欠損を同じ状態に変換する契約は未採用 |

根拠はpacket_cli.generate、packet.build_proposals/render_packet、opsのgate、およびtest_packet_mapping_safetyの既定値互換試験、共通test_gateのUNKNOWN拒否試験。通常の日次リハーサルは人工lots/eventsを明示生成するため、この既定値の問題を実データ解決済みとは扱わない。

## 次の判断との関係

[限定契約の比較表](NEXT_CONTRACT_DECISIONS.md)の市場入力3項目に対応する判断材料である。厳格な新modeでの部分調整拒否、行/run単位の公表日由来、検証済み単元・イベント原本の必須化は未採用。既存研究結果の再計算、旧home移行、実API接続をこの資料から自動開始しない。
