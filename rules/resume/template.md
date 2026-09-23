# Шаблон резюме

Сохраняй следующую последовательность разделов:

1. Центрированная шапка в HTML-обёртке.
2. Горизонтальная линия.
3. `PROFESSIONAL SUMMARY`.
4. `CORE SKILLS`.
5. `PROFESSIONAL EXPERIENCE`.
6. `EDUCATION`.
7. `LANGUAGES`.

Используй этот каркас:

```md
<div style="text-align: center">

# {{CANDIDATE_NAME}}

**{{TARGET_ROLE_TITLE}}**

[{{CANDIDATE_EMAIL}}](mailto:{{CANDIDATE_EMAIL}}) • [{{CANDIDATE_TELEGRAM}}]({{CANDIDATE_TELEGRAM}}) • [{{CANDIDATE_LINKEDIN}}]({{CANDIDATE_LINKEDIN}}) • {{CANDIDATE_LOCATION}}

</div>

---

## PROFESSIONAL SUMMARY

{{SUMMARY}}

## CORE SKILLS

**{{SKILL_GROUP_1}}:** {{SKILLS_1}}

**{{SKILL_GROUP_2}}:** {{SKILLS_2}}

**{{SKILL_GROUP_3}}:** {{SKILLS_3}}

**{{SKILL_GROUP_4}}:** {{SKILLS_4}}

## PROFESSIONAL EXPERIENCE

### {{COMPANY_1}}&nbsp;— {{ROLE_1}}   |   **{{DATES_1}}**

{{OPTIONAL_SCOPE_SENTENCE}}

- {{RELEVANT_ACHIEVEMENT}}
- {{RELEVANT_ACHIEVEMENT}}
- {{RELEVANT_ACHIEVEMENT}}
- {{RELEVANT_ACHIEVEMENT}}

### {{COMPANY_2}}&nbsp;— {{ROLE_2}}   |   **{{DATES_2}}**

- {{RELEVANT_ACHIEVEMENT}}
- {{RELEVANT_ACHIEVEMENT}}

### {{COMPANY_3}}&nbsp;— {{ROLE_3}}   |   **{{DATES_3}}**

- {{RELEVANT_ACHIEVEMENT}}

## EDUCATION

**{{DEGREE_AND_UNIVERSITY}}** · {{DATES}}

## LANGUAGES

{{LANGUAGES}}
```

Каждая строка `CORE SKILLS` в этом каркасе является отдельной компактной
группой. После рендера она должна целиком оставаться на одной визуальной строке;
следующая визуальная строка всегда должна начинаться уже с новой обобщающей
категории.

Добавляй дополнительные места работы или подраздел релевантных проектов только
тогда, когда они усиливают соответствие вакансии и итог по-прежнему проходит
одностраничную проверку.
