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

    拒否リストの語やメールアドレスの形がリクエストボディに含まれていた場合に送出する。
    メッセージは固定文言で、一致した語やリクエスト本文を含めない。
    """
    pass


class LlmSendGuardUnavailableError(LLMError):
    """拒否リストが指定されているのに使えないため送信しなかったときにスローされる例外。

    リストファイルが読めない・JSON でない・文字列配列でない場合に送出する
    （検査できない状態では送らない）。メッセージにリストの中身は含めない。
    """
    pass
