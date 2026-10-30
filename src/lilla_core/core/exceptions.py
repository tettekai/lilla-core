class ReauthenticationRequiredError(Exception):
    """外部APIを呼び出す際に再認証が必要になったなときにスローされる例外。"""
    pass


class ConversationNotFoundError(LookupError):
    """差分取得の起点に指定した会話履歴のドキュメントが見つからないときにスローされる例外。

    `ConversationRepository.list_for_client(after_id=...)` で、`after_id` の
    ドキュメントが存在しない（TTL で消えた・削除された・別 DB の id など）うえ、
    代わりに使える `since` も渡されていない場合に送出する。呼び出し側は
    「起点が失われたので全件を取り直す」合図として扱う（HTTP なら 404 / 410 など）。
    """
    pass


class LLMError(Exception):
    """LLM 呼び出しに失敗したときにスローされる例外。

    Ollama / OpenAI 互換プロバイダへのリクエスト送信やレスポンス解析で
    発生した例外をラップし、呼び出し側が LLM 由来の失敗を型で識別できるようにする。
    """
    pass


class LlmSendBlockedError(LLMError):
    """LLM へ送る直前の検査で送信を止めたときにスローされる例外。

    拒否リストの語がリクエストボディに含まれていた場合に送出する。
    メッセージは固定文言で、一致した語やリクエスト本文を含めない。
    """
    pass


class LlmSendGuardUnavailableError(LLMError):
    """拒否リストが指定されているのに使えないときにスローされる例外。

    リストファイルが読めない・JSON でない・文字列配列でない・空文字を含む場合に送出する。
    `bot.py` の起動時に出れば起動を止め、起動時の読み込みを経ない呼び出しでは送信しない。
    メッセージにリストの中身は含めない。
    """
    pass
