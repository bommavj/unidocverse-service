from app.services.domain_agents.insurance_agent    import InsuranceAgent
from app.services.domain_agents.legal_agent        import LegalAgent
from app.services.domain_agents.medical_agent      import MedicalAgent
from app.services.domain_agents.finance_agent      import FinanceAgent
from app.services.domain_agents.hr_agent           import HRAgent
from app.services.domain_agents.real_estate_agent  import RealEstateAgent
from app.services.domain_agents.supply_chain_agent import SupplyChainAgent
from app.services.domain_agents.compliance_agent   import ComplianceAgent
from app.services.domain_agents.operations_agent   import OperationsAgent
from app.services.domain_agents.immigration_agent  import ImmigrationAgent

AGENT_REGISTRY = {
    "insurance":    InsuranceAgent,
    "legal":        LegalAgent,
    "medical":      MedicalAgent,
    "finance":      FinanceAgent,
    "hr":           HRAgent,
    "real_estate":  RealEstateAgent,
    "supply_chain": SupplyChainAgent,
    "compliance":   ComplianceAgent,
    "operations":   OperationsAgent,
    "immigration":  ImmigrationAgent,
}


def get_agent_class(agent_type: str):
    return AGENT_REGISTRY.get(agent_type)
