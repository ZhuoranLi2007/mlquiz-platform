from pathlib import Path
for name in ['frontend/src/views/TeacherAttemptReviewView.vue','frontend/src/components/StudentReviewQuestion.vue']:
 p=Path(name);s=p.read_text(encoding='utf-8')
 # Only the true/false branch has choice.label; do not change ordinary option widths.
 s=s.replace(':key="choice.label" class="option"',':key="choice.label" class="option boolean-option"')
 if '.option.boolean-option{' not in s:s=s.replace('</style>', '.option.boolean-option{grid-template-columns:max-content minmax(0,1fr)}.boolean-option>b{white-space:nowrap}.boolean-option .option-note{grid-column:2;justify-self:end}\n</style>',1)
 p.write_text(s,encoding='utf-8')
