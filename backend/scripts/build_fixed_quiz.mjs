import { readFileSync, writeFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

// This script runs only the checked-in prototype fixture, never user input.
const fixture = new URL("../../prototype/questions.js", import.meta.url);
const output = new URL("../data/topics/fixed-seasons.json", import.meta.url);
const window = Object.create(null);

runInNewContext(readFileSync(fixture, "utf8"), { window }, {
  filename: fixture.pathname,
  timeout: 1_000,
  contextCodeGeneration: { strings: false, wasm: false },
});

const { topics } = window.QINGZHI_DATA;
const { title, takeaways, questions } = topics.find((topic) => topic.id === "seasons");
const supplementalEvidence = { "seasons-17": ["earth-weather"] };

const quiz = {
  title,
  takeaways: takeaways.map(({ text, evidence }) => ({ text, evidenceIds: [evidence] })),
  questions: questions.map(({ id, type, stem, options, correct, explanation, evidence }) => {
    const normalizedOptions = options.map((text, index) => ({ optionId: `o${index + 1}`, text }));
    return {
      questionId: id,
      type,
      stem,
      options: normalizedOptions,
      correctOptionIds: correct.map((index) => normalizedOptions[index].optionId),
      explanation,
      evidenceIds: [evidence, ...(supplementalEvidence[id] ?? [])],
    };
  }),
};

writeFileSync(output, `${JSON.stringify(quiz, null, 2)}\n`, "utf8");
