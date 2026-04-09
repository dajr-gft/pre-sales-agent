from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext


def request_customer_logo(tool_context: ToolContext) -> dict:
    """Signal that the agent is ready to receive the customer logo.

    Call this tool at the beginning of Phase 3 (Document Assembly),
    before asking the user to upload the logo. It activates the
    logo interceptor callback, which will capture the next image
    upload and save it for document insertion.
    """
    tool_context.state['awaiting_logo'] = True
    return {
        'status': 'awaiting_logo',
        'message': 'Estado atualizado. Pergunte ao usuário pelo logo do cliente.',
    }
