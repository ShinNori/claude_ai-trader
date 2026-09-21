# v0.4.1 ログのマスク後キー衝突レビュー

Codex並列担当による検証・修正。開始2026-09-11 04:30 JST、終了04:31 JST。Claudeによる独立再レビューではない。

## 発見と分類

辞書の文字列キーを秘密値マスク・64KB切詰めしてから辞書へ戻す処理は、異なる元キーが同じ表示名になった場合、先の値を上書きしていた。例えばダミートークンをキーにした`first`と、置換先文字列`<LINE_CHANNEL_ACCESS_TOKEN>`をキーにした`second`を同時に渡すと、ログから`first`が消える。長文キーの切詰めでも同様。

追加8ケース：両judge × 秘密値衝突・長文切詰め衝突・リスト内ネスト衝突＝6ケースと、両judgeの非衝突形式維持＝2ケース。修正前は**6 failed / 2 passed（0.16秒）**。6失敗は同じ実装不具合A、前提誤りBは0。公開`review()`をstub経由で呼び、JSONLを読んで確認した。実CLI・通信はfixtureで禁止。

## 採用するログ表現

衝突した辞書だけを次の表現にする。

```json
{"__masked_key_collision__": [{"key": "マスク済みキー", "value": "マスク済み値"}]}
```

配列は元辞書の挿入順を維持し、同じマスク済みキーが複数あっても全エントリと値を保持する。元の秘密キーを別項目に残さない。元辞書に`__masked_key_collision__`というキーがあっても、その項目を配列内へ保存するため上書きしない。各値の再帰マスクは従来どおり行う。衝突しない辞書の既存形状、64KB上限、戻り値Verdict、呼出側の入力は維持する。

このマーカーはログを読む際の表示形式であり、外部入力の信頼性を保証する署名や型証明ではない。元データに同形のオブジェクトが存在し得るため、セキュリティ判定の根拠にしない。秘密値マスクと切詰めによって失われる元キーそのものは復元しない。

## 修正後の確認

追加8件＋既存judges受入69件＋既存v0.4.1反証19件＝**96 passed（0.75秒）**、skip/xfailなし。既存テスト変更・弱体化なし。全体試験は親担当が統合後に実行するため、本報告は全体通過を主張しない。

ops/から同梱Pythonで実行。`PYTHONDONTWRITEBYTECODE=1`、`PYTHONIOENCODING=utf-8`を設定。

```text
python -m pytest ../build-codex/tests/test_ops_v041_mask_review.py ../common/tests/phase2/test_judges.py ../build-codex/tests/test_ops_v041_review.py -q -p no:cacheprovider --basetemp C:\Users\s\AppData\Local\Temp\ai-trader-mask-review
```

編集対象は`ops/aitrader_ops/judges.py`、`build-codex/tests/test_ops_v041_mask_review.py`、本書のみ。Claudeへの引き渡し不要。
