class ReauthenticationRequiredError(Exception):
    """外部APIを呼び出す際に再認証が必要になったなときにスローされる例外。"""
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
