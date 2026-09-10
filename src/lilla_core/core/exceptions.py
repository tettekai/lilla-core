class ReauthenticationRequiredError(Exception):
    """外部APIを呼び出す際に再認証が必要になったなときにスローされる例外。"""
    pass


class LLMError(Exception):
    """LLM 呼び出しに失敗したときにスローされる例外。

    Ollama / OpenAI 互換プロバイダへのリクエスト送信やレスポンス解析で
    発生した例外をラップし、呼び出し側が LLM 由来の失敗を型で識別できるようにする。
    """
    pass
