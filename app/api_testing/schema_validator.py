from app.core.exceptions import ApiDocumentError
from app.schemas.api_schema import ApiSchemaDocument


class ApiSchemaValidator:
    def validate(self, schema: ApiSchemaDocument) -> ApiSchemaDocument:
        if not schema.endpoints:
            raise ApiDocumentError("api_document_insufficient: no endpoints")
        seen: set[tuple[str, str]] = set()
        for endpoint in schema.endpoints:
            key = (endpoint.method, endpoint.path)
            if key in seen:
                raise ApiDocumentError(
                    f"duplicate endpoint in generated schema: {endpoint.method} {endpoint.path}"
                )
            if not endpoint.path.startswith("/"):
                raise ApiDocumentError(f"endpoint path must start with '/': {endpoint.path}")
            seen.add(key)
        return schema

