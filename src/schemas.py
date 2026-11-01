# -*- coding: utf-8 -*-
"""MedIR-CMCR Pydantic 数据契约（对齐开发计划书第三章）。

5 大核心结构：
1. CMCRNativeCase        —— 原生结构化病历（含离线命题集 F 槽位）
2. ClinicalProposition   —— 离线查询无关原子临床命题 f_j（公式 2）
3. JevVerifiedFact       —— Jev 双问题事实核验结果（公式 3 三态）
4. VitalSigns            —— 数值型生命体征
5. InvolvedSpecialty     —— 由病例真实救治链派生的科室（零预设枚举）
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class VitalSigns(BaseModel):
    temperature_c: Optional[float] = Field(None, description="体温 (℃)")
    systolic_bp_mmhg: Optional[int] = Field(None, description="收缩压 (mmHg)")
    diastolic_bp_mmhg: Optional[int] = Field(None, description="舒张压 (mmHg)")
    heart_rate_bpm: Optional[int] = Field(None, description="心率 (次/min)")
    respiratory_rate_bpm: Optional[int] = Field(None, description="呼吸频率 (次/min)")
    spo2_percent: Optional[float] = Field(None, description="脉搏血氧饱和度 (%)")


class InvolvedSpecialty(BaseModel):
    specialty: str = Field(..., description="由病例真实救治链产生的科室名称，不预设固定分类")
    role: str = Field(..., description="该科室在该病例诊治链中的具体职能")


class ClinicalProposition(BaseModel):
    """对应公式 (2): F = {(f_j, d_{s_j})}_{j=1}^M"""
    prop_id: str = Field(..., description="命题唯一ID，如 F_1673770_01")
    source_cmaid: str = Field(..., description="所属源病例编号 d_{s_j}")
    element_category: str = Field(..., description="要素类别：核心症状与诱因 | 既往病史 | 阳性查体 | 急危红旗征 | 治疗反应")
    proposition_zh: str = Field(..., description="单句、通俗、可独立核验的临床事实命题 f_j (中文)")
    proposition_en: Optional[str] = Field(None, description="英文对照（供论文与审稿人展示）")
    mention_keywords: List[str] = Field(default_factory=list, description="触发 Mention 检查的关键词")
    affirm_keywords: List[str] = Field(default_factory=list, description="触发阳性肯定的关键词")
    negate_keywords: List[str] = Field(default_factory=list, description="触发明确否定的关键词")
    uncertain_keywords: List[str] = Field(default_factory=list, description="触发未知/未检查的关键词")
    is_red_flag: bool = Field(False, description="是否为危及生命的急诊红旗征要素")
    clarification_question_zh: str = Field("", description="absent/gap 时的追问话术")
    follow_up_chip: str = Field("", description="前端一键追问胶囊按钮短语")


class JevVerifiedFact(BaseModel):
    prop_id: str
    source_cmaid: str
    proposition_zh: str
    proposition_en: Optional[str] = None
    is_red_flag: bool = False
    p_men: float = Field(..., ge=0.0, le=1.0, description="Step 1: Fact Mention 概率")
    p_sup: float = Field(..., ge=0.0, le=1.0, description="Step 2: Fact Support 概率")
    role: Literal["supported", "gap", "absent"] = Field(
        ...,
        description="supported (p_sup>=tau); gap (p_sup<tau and p_men>=tau); absent (p_men<tau)",
    )
    matched_evidence_span: List[str] = Field(default_factory=list, description="患者查询中命中的文本片段")
    follow_up_chip: str = ""


class CMCRNativeCase(BaseModel):
    """CMCR 原生结构化病历。clinical_record 保留原生小节树 + 启发式映射，零预设科室。"""
    cmaid: str
    doi: Optional[str] = None
    title: str
    url: str
    reporting_institution: str = ""
    reporting_department: str = ""
    authors: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    pub_year: Optional[str] = None
    is_open_access: bool = False
    structured_abstract: Dict[str, str] = Field(default_factory=dict)
    clinical_record: Dict[str, Any] = Field(default_factory=dict)
    native_sections: List[Dict[str, Any]] = Field(default_factory=list, description="正文原生小节树（逐字保留）")
    extracted_vital_signs: Dict[str, Any] = Field(default_factory=dict)
    red_flag_candidates: List[str] = Field(default_factory=list)
    involved_specialties: List[InvolvedSpecialty] = Field(default_factory=list)
    propositions: List[ClinicalProposition] = Field(default_factory=list, description="离线命题集 F (M<=100)，Task 2 填充")
    completeness: Dict[str, Any] = Field(default_factory=dict, description="核心小节防幻觉完整性校验结果")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="来源溯源（源XML路径/解析时间）")
