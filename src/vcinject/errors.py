class InjectError(Exception):
    """A user-facing error: the message is shown as-is by the CLI and GUI."""


class MissingResource(InjectError):
    """Something vcinject needs is missing or damaged and can be set up again.

    ``resource`` names it so the CLI and GUI can offer the matching fix:
    ``tool:<name>``, ``gba-template``, ``vclook:<system>`` or ``forwarder:<kind>``.
    """

    def __init__(self, resource: str, message: str):
        super().__init__(message)
        self.resource = resource
