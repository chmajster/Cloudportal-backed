from app.api.schemas import BlueprintInput, HostnameSchemeInput, Input


class BlueprintBundleInput(Input):
    blueprint: BlueprintInput
    hostname_scheme: HostnameSchemeInput | None = None
