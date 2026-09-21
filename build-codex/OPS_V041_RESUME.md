# v0.4.1 再開記録（Codex、2026-09-11）

開始04:23 JST、実測終了04:24 JST。ユーザーが親権限継承の修復成功（1件成功・0件失敗）を報告し、Codexのapply_patchでも新規テストの保存に成功した。第17回の成果物保存問題は解消を確認。自動見張りのブロック解除・再起動は行っていない。

ユーザーの「Claudeも利用制限掛かったのでCodexで作業続けたい」に基づき、今回の不具合修正はCodexがopsを代行した。独立したClaude再レビューは未実施。

## 反証と修正

test_ops_v041_review.py に6ケースを追加。両judgeについて、ネストした配列内の秘密値、キー名の部分文字列としての秘密値、64KB超の日本語長文末尾を検査する。秘密はテスト用ダミーのみ。

初回702件：700 passed / 2 failed in 18.20s。既存696件は通過。追加6件は4通過・2失敗。W01 nested_key の両judgeがA（実装不具合）：_mask_deepが辞書の値だけをマスクし、キー名に含まれる秘密値をログへ出していた。B（前提誤り）は0件。

ops/aitrader_ops/judges.py で文字列キーにも既存_maskを適用。戻り値のVerdictは変更せず、ログ用コピーだけを処理する。既存試験の弱体化なし。

修正後：**702 passed in 15.95s**、終了コード0、skip/xfailなし。

実行はops/で、PYTHONDONTWRITEBYTECODE=1、PYTHONIOENCODING=utf-8、同梱Pythonを使用。

```text
python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q -p no:cacheprovider --basetemp C:\Users\s\AppData\Local\Temp\ai-trader-pytest
```

## 残作業

これは第17回全体の独立レビュー完了ではない。以前未保存だった36ケースを復元したという意味でもなく、今回実際に追加したものは6ケース。V10の時計ずれ・タイムゾーン・欠損、月境界/STOP/予算の組合せ、署名検証と重複判定の追加反証、仕様案v0.2の契約反映、自動化README注意書き、最終引き渡し公開は未完了。マスク後のキー衝突によるログ情報欠落も追加検討対象。

Claudeへの実送信、実LLM、LINE、実口座への接続は行っていない。売買シグナルの二重承認条件は維持する。

## 2026-09-11 04:28 JST 続行結果

追加13ケース（累計19）：W02制御時刻欠損/型違い/巨大値8、W03 UTCで同時刻/1ms未来のSTOP/RESUME4、W04署名→重複/競合の順序1。追加群実測は17通過・2失敗（19件中、巨大timestampのSTOP/RESUME）。A2/B0。日時変換のOverflowError/OSErrorをValueErrorへ変換し、既存のINVALID応答へ接続した。広範囲な例外握り潰しはしない。

修正後全体 **715 passed in 16.13s**、終了コード0、skip/xfailなし。前回と同じDropbox外basetemp、キャッシュ無効、実通信禁止。仕様案v0.2 §8にV10採用・時計ずれ許容0・STALE_CLAIM10分の位置付けとAPI契約を反映。

残作業は月境界/STOP/予算の追加組合せ検証、マスク後のキー衝突時の情報保持、自動化READMEの注意書き。第17回全体の完了とはしない。Claudeへの引き渡し公開はユーザーのCodex単独方針により不要。

## 2026-09-11 07:56 JST — Codex単独での統合完了

並列反証を統合し、全体 **743 passed in 18.82s**（終了コード0、skip/xfailなし）。基準696件に追加47件（先行19＋マスク8＋予算20）。最終テスト前提誤りBは0。修正前に検出したAは22ケース（キー漏出2、時刻範囲2、マスク後キー衝突6、月予算12）で、全て修正後通過。予算試験の初期フィクスチャ誤認は個別報告に履歴を残した。

UNKNOWN再試行の通常完了・例外経路で予約済みmonthを保持し、旧月枠が新月へ移る不具合を修正。期限切れSENDING回収も同じ扱い。未予約時のみ送信月を補完し、明確な新規送信失敗は枠を解放する。実LINE課金を検証したという意味ではない。

マスク後の同名キーは衝突した辞書だけ順序付きエントリ列へ変換し、値の上書きを防止。外部入力に同形マーカーがあり得るため、マーカーを信頼性の証明には使わない。

全体実測: ops/ で python -m pytest ../common/tests/phase2 ../build-codex/tests tests -q -p no:cacheprovider --basetemp C:\Users\s\AppData\Local\Temp\ai-trader-pytest。PYTHONDONTWRITEBYTECODE=1、PYTHONIOENCODING=utf-8。同梱Python使用、実口座・実LINE・実審査LLM未接続。Claudeによる独立再レビューではなく、Codexによる修正と検証。

第17回devの修正・反証・仕様案・README追記は完了。autoチャネルのエンジン修正と旧見張り停止状態の解除は別件で未実施。Claudeへの公開・起動はユーザー方針により行わない。

詳細: [予算20ケース](OPS_V041_BUDGET_REVIEW.md)、[マスク8ケース](OPS_V041_MASK_REVIEW.md)。引き渡し不要。
