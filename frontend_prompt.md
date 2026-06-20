We're at the stage where we have our data generated and our states logged on our database. The next phase would be the frontend, where we want to demonstrate a baseline interaction from the user. The interaction mechanism should be very simple:

1) On the UI, I should see a list of ALL the texts/stories I have access to (if there's only one, I should be able to click it like a card)

2) Once I click into a text, I should be able to scroll through the text, have a small window with story properly formatted. I should be able to highlight part of the text (the highlighted part should be yellow). Once I finish highlighting a text, there should be a "query" button that activates. I click the button and the 'state' mapping to where the text is located should be queried (if there's an overlap in sections, return them all). The data retrieved is context for the next phase (think Kindle reader).

3) This 'raw data' that's been extracted would then be thrown into an agent that consolidates all the context its given in terms of where the story is and what the state is. With that, another full prompt would be generated for an audio and video model to intercept as text input. The video model should be able to take in the input provided to generate a complete video scene that reflects the selected text and the context of the book. For now, there's no video model enabled, so just console.log the prompt once the 'submit' button is clicked. That's all I'd like for now.

Note: Please build the frontend using React.js + Tailwind CSS. 
