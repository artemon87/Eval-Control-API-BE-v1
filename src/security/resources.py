from src.models.authorization import ResourceContext
from src.security.permissions import ResourceType


def evaluation_resource(eval_type: str) -> ResourceContext:
    return ResourceContext(
        resource=ResourceType.EVALUATION,
        attributes={
            "eval_type": eval_type,
        },
    )