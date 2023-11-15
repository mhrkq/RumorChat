import requests
import pandas as pd
import os
import html

def main():
    INPUT_FILE = "mass_infer_data.xlsx"
    OUTPUT_FILE = "mass_infer_responses.csv"
    CHATBOT_HOST = "127.0.0.1:6000"
    CHATBOT_URI = f"http://{CHATBOT_HOST}/api/v1/chat"
    OUTPUT_FOLDER = "data"  # Default, should NOT be changed

    output_path = os.path.join(OUTPUT_FOLDER, OUTPUT_FILE)

    # Create the directory if it does not exist
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    dummy_request = {
        "user_input": "Say yes.",
        "max_new_tokens": 10,
        "auto_max_new_tokens": False,
        "max_tokens_second": 0,
        "history": {"internal": [], "visible": []},
        "mode": "instruct",  # Valid options: 'chat', 'chat-instruct', 'instruct'
        "character": "Example",
        "instruction_template": "Vicuna-v1.1",  # Will get autodetected if unset
        "your_name": "You",
        # 'name1': 'name of user', # Optional
        # 'name2': 'name of character', # Optional
        # 'context': 'character context', # Optional
        # 'greeting': 'greeting', # Optional
        # 'name1_instruct': 'You', # Optional
        # 'name2_instruct': 'Assistant', # Optional
        # 'context_instruct': 'context_instruct', # Optional
        # 'turn_template': 'turn_template', # Optional
        "regenerate": False,
        "_continue": False,
        "chat_instruct_command": 'Continue the chat dialogue below. Write a single reply for the character "<|character|>".\n\n<|prompt|>',
        # Generation params. If 'preset' is set to different than 'None', the values
        # in presets/preset-name.yaml are used instead of the individual numbers.
        "preset": "None",
        "do_sample": True,
        "temperature": 0.7,
        "top_p": 0.1,
        "typical_p": 1,
        "epsilon_cutoff": 0,  # In units of 1e-4
        "eta_cutoff": 0,  # In units of 1e-4
        "tfs": 1,
        "top_a": 0,
        "repetition_penalty": 1.18,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "repetition_penalty_range": 0,
        "top_k": 40,
        "min_length": 0,
        "no_repeat_ngram_size": 0,
        "num_beams": 1,
        "penalty_alpha": 0,
        "length_penalty": 1,
        "early_stopping": False,
        "mirostat_mode": 0,
        "mirostat_tau": 5,
        "mirostat_eta": 0.1,
        "grammar_string": "",
        "guidance_scale": 1,
        "negative_prompt": "",
        "seed": -1,
        "add_bos_token": True,
        "truncation_length": 2048,
        "ban_eos_token": False,
        "custom_token_bans": "",
        "skip_special_tokens": True,
        "stopping_strings": [],
    }


    # Function to read the input file based on its extension
    def read_input_file(file_path):
        _, file_extension = os.path.splitext(file_path)
        if file_extension.lower() == ".csv":
            return pd.read_csv(file_path)
        elif file_extension.lower() in [".xls", ".xlsx"]:
            return pd.read_excel(file_path)
        else:
            raise ValueError("Unsupported file type. Please provide a .csv or .xlsx file.")


    # Function to check server connectivity
    def check_server_connectivity(uri):
        try:
            response = requests.post(CHATBOT_URI, json=dummy_request)
            return response.status_code == 200
        except requests.RequestException as e:
            print(f"Error checking server connectivity: {e}")
            return False


    # Check if the LLM server can be connected to
    if not check_server_connectivity(CHATBOT_URI):
        print("Cannot connect to the LLM server. Please check the server status.")
    else:
        print("LLM server is running. Starting inference process...")
        try:
            df = read_input_file(INPUT_FILE)
            # Replace the string literals with actual newline characters
            df.replace({r"\\n": "\n"}, regex=True, inplace=True)
        except ValueError as e:
            print(e)
        # Prepare a DataFrame to store responses
        responses = []

        history = {"internal": [], "visible": []}

        # Iterate over each row in the DataFrame
        for index, row in df.iterrows():
            request_data = {
                "user_input": row["user_input"],
                "max_new_tokens": 500,
                "auto_max_new_tokens": False,
                "max_tokens_second": 0,
                "history": history,
                "mode": "instruct",  # Valid options: 'chat', 'chat-instruct', 'instruct'
                "character": "Example",
                "instruction_template": "Vicuna-v1.1",  # Will get autodetected if unset
                "your_name": "You",
                # 'name1': 'name of user', # Optional
                # 'name2': 'name of character', # Optional
                # 'context': 'character context', # Optional
                # 'greeting': 'greeting', # Optional
                "name1_instruct": row["name1_instruct"],
                "name2_instruct": row["name2_instruct"],
                "context_instruct": row["context_instruct"],
                "turn_template": row["turn_template"],
                "regenerate": False,
                "_continue": False,
                "chat_instruct_command": 'Continue the chat dialogue below. Write a single reply for the character "<|character|>".\n\n<|prompt|>',
                # Generation params. If 'preset' is set to different than 'None', the values
                # in presets/preset-name.yaml are used instead of the individual numbers.
                "preset": "None",
                "do_sample": True,
                "temperature": 0.7,
                "top_p": 0.1,
                "typical_p": 1,
                "epsilon_cutoff": 0,  # In units of 1e-4
                "eta_cutoff": 0,  # In units of 1e-4
                "tfs": 1,
                "top_a": 0,
                "repetition_penalty": 1.18,
                "repetition_penalty_range": 0,
                "top_k": 40,
                "min_length": 0,
                "no_repeat_ngram_size": 0,
                "num_beams": 1,
                "penalty_alpha": 0,
                "length_penalty": 1,
                "early_stopping": False,
                "mirostat_mode": 0,
                "mirostat_tau": 5,
                "mirostat_eta": 0.1,
                "grammar_string": "",
                "guidance_scale": 1,
                "negative_prompt": "",
                "seed": -1,
                "add_bos_token": True,
                "truncation_length": 2048,
                "ban_eos_token": False,
                "custom_token_bans": "",
                "skip_special_tokens": True,
                "stopping_strings": [],
            }

            try:
                # Send request to the server
                response = requests.post(CHATBOT_URI, json=request_data)

                # Process the response
                if response.status_code == 200:
                    results = response.json()["results"]
                    chatbot_reply = results[0]["history"]["visible"][-1][1]
                    # Decode HTML entities in the response
                    chatbot_reply = html.unescape(chatbot_reply)
                else:
                    print(f"Error with status code: {response.status_code}")
                    chatbot_reply = "Error occurred during request."

            except requests.RequestException as e:
                print(f"Request failed: {e}")
                chatbot_reply = "Server error occurred."
            print(f"Row {index+1} response: {chatbot_reply}")
            responses.append(
                {"user_input": row["user_input"], "chatbot_reply": chatbot_reply}
            )

        # Convert the list of dicts to a DataFrame
        responses_df = pd.DataFrame(responses)

        # Combine the input df with responses_df to include both user_input and chatbot_reply
        final_df = pd.concat([df.iloc[:, :-1], responses_df], axis=1)

        # Save the final DataFrame to a new CSV file
        final_df.to_csv(output_path, index=False)
        print("Inference process completed. Responses saved to 'mass_infer_responses.csv'.")

if __name__ == "__main__":
    main()
