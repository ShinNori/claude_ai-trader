# ops v0.4.0 独立レビュー（Codex第16回）

## 結果と実行環境

導入済みの同梱Pythonをこのデスクトップの許可済みユーザー環境で実行。初回633 passed in 18.95s。追加49件を含む最終は **682件＝667 passed / 15 failed in 17.13s、skip/xfailなし**。既存633件維持、追加49件＝34通過・15失敗。全件通過ではない。実行ディレクトリops/、コマンド本体 python -B -m pytest ../common/tests/phase2 ../build-codex/tests tests -q -p no:cacheprovider --tb=short。Pythonは C:/Users/s/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe。別依存環境やPYTHONPATHへの切替なし。合成一時台帳とstubのみ。

共有作業ツリーには別Codex実行の11:15記録（131 passed / 502 errors、pytest-of-sへのアクセス拒否）が後着した。この記録は子CLI sandboxの結果で、今回の通常ユーザー環境の結果と区別する。子CLIの環境問題は未解決で、QUESTIONS.mdのマーカー・ACL・監視状態は変更しない。今回の実測でその502件を実装不具合と認定したり、子CLI修復済みと宣言したりしない。

## 失敗の分類と修正案

A＝既存契約の実装不適合、B＝解釈の明確化が必要な提案。テスト前提の誤りとして除外した失敗はなし。Bの期待値は提案として明示し、採否をClaudeへ依頼する。

| ID | ケース数 | 分類 | 再現・影響・修正案 |
|---|---:|---|---|
| V01 | 3 | A | judges.py:256付近はstdout/final_messageだけをマスク。reason→verdict/raw_answer、packet、追加stderrにダミー秘密値が平文で残る。ログ全体へ再帰的マスク、recordの許可キー化・stderr制限を適用する。実秘密値は使用していない。 |
| V02 | 1 | A | claim後に送信関数が例外で落ちるとSENDINGのまま。再起動後flush対象外、警告なし、monthly_used=0。送信成否を断定せず同じretry_keyと予約を保持し、回復/照合可能にする。 |
| V03 | 1 | A | 予算1、異なる2行、別接続のflush。一方をclaim後に停止させると他方も送信し、送信呼出2回。予算確認がトランザクション外、SENDINGが計上されない。予算予約・再確認とclaimを同じトランザクションに入れる。古い行スナップショットの無条件UPDATEによるSENT巻戻しも防ぐ。 |
| V04 | 1 | A | NEWをtimeout→07:16にflushするとUNKNOWNがEXPIREDになり、予算1→0。成否不明と候補期限を別軸で保持し、再送を止めても未送信と断定せず警告・予算を維持。 |
| V05 | 1 | A | timeout後の同一retryでfailureとなるとPENDING化し予算0。後続試行の不受理だけでは最初の受理有無は解消しない。過去UNKNOWNの記憶と予約を維持し、照合結果なしに解除しない。 |
| V06 | 1 | A | 前月のNO_SIGNAL待機行が新月flushで送られる。日次評価日・失効と新月予算を区別し、古い日次通知を自動一括配信しない。 |
| V07 | 1 | A | 09:00STOP→10:00RESUME後、08:00のSTOPが11:00に遅着すると再停止。制御がevent.timestampでなく受信nowだけを使っている。認証済み制御の業務時刻と受信時刻を別保存し古い操作で新しい操作を巻き戻さない。 |
| V08 | 2 | A | 署名正常のmessage/postbackがlistだと.items/.getでAttributeErrorとなり後続の正常イベントも未処理。イベント型の検査でINVALIDに畳み込み、永続化障害とは区別する。 |
| V09 | 1 | A | 非ASCII署名でcompare_digestがTypeError。形式不正署名を401で拒否し、未認証入力で例外を漏らさない。 |
| V10 | 2 | B | 未来timestampのSTOP/RESUMEが受理される。未来日時拒否を制御にも適用する提案。STOP即時優先を採る場合は例外・監査・RESUMEとの違いを明記する。提案を無断で仕様確定しない。 |
| V13 | 1 | A | 全角ｍの https://ｍember.rakuten-sec.co.jp/?code={code} が通る。ブラウザのドメインASCII化で会員ホストに正規化され得るため、member禁止の検証前に正規化するか非ASCIIホストを拒否する。 |

計A13ケース、B2ケース。根拠コードはnotify.pyのbroker_link:64、_used:287、flush:341、verify_signature:438、_business_payload:445、_apply_event:488、judges.pyのログ構成:256以降。

V03の最初の試験はmonthly_usedだけを検査していたが、古いrowによる状態上書きで送信回数を見落とすため、合成送信関数の実呼出回数も検査するよう強化した。skip/xfail/条件緩和ではない。送信後の合計集計だけを並行安全性の証拠にしない。

## 通過した境界と検証の限界

V11は両judgeの大小文字・数値文字列・ネストqty・余分キーを拒否（10ケース）。V12は07:15より前/後のexpires_at、同時刻/1µs超過、UTC表記（4）。V13の大文字host・末尾ドット・443・path内@は受理、偽接尾辞hostは拒否（4通過/IDN1失敗）。非標準ポートの許容は現仕様で未決のため一律拒否の新契約にはしない。

V14はNEEDS_DETAILS→新eventで部分40株→累計超過拒否→残60株→遅着ORDEREDのIGNORED（1）。V15はreplay記録のhash改変拒否と原本不変（1）。一致hashのままreason/decisionを変更したファイルの暗号学的真正性はAPIに期待hash/署名がないため保証しない。

V16は両CLI計画の純粋生成・固定ツール制限・最終応答指定・transport=cli拒否（2）。隔離ホーム、hooks/plugins/MCP、推論通信だけの許可、子孫回収は未実装であり、network_access=Falseを実効OS隔離の証拠にしない。実起動はしていない。

V17はUNKNOWNの24h同時刻/1µs超過、同retry_key（2）。V18はJST月末23:59:59→翌月0:00をUTC引数でも分ける（1）。V19はSTOP中のRISK/RECONCILE継続（2）。V20は生body改行/BOMと大文字base64を401（3）。V21はrecordのstdout/exit_code/elapsed/final_message型不正をINVALID（4）。既存633件のSTOPファイルOR・EXIT・reconcile_sent等も維持。ただし全入力組合せや全クラッシュ点を網羅したとは扱わない。新規Notifierはfixtureで全closeする。既存受入試験のclose不足は今回は未編集。

URL正規化の根拠: [WHATWG URL StandardのIDNA](https://url.spec.whatwg.org/#idna)。標準のドメインASCII化を踏まえたV13の判断であり、楽天へのアクセスや実ブラウザでの会員ページ閲覧は行っていない。

## 成果物・引き渡し

[追加49ケース](tests/test_ops_v040_review.py)、[仕様案v0.2](共通仕様_フェーズ2_順序4_修正提案_v0.2.md)、[次のClaude依頼](Claude引き渡しプロンプト.md)。ops実装・共通仕様本文・既存試験は未変更。自動連携autoのE系列は今回対象外。開始時刻は未取得。最初の試験から追加試験まで本記録の数値を採用し、別実行の環境エラー記録は履歴として保持。
終了時刻: 2026-09-09 11:18 JST。次依頼はClaude向けMDに保存・公開。子CLI環境質問は未解除。
