import { buildTwentyQuestionsPrompt } from "./twentyQuestions";

export function buildPrompt({
  topic,
  name,
  training,
}: {
  topic?: string;
  name?: string;
  training?: boolean;
}): string {
  return buildTwentyQuestionsPrompt(topic || "", name || "Avatar", {
    training: !!training,
  });
}

export { buildTwentyQuestionsPrompt };
