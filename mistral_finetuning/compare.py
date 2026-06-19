import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

BASE = "mistralai/Mistral-7B-Instruct-v0.3"
ADAPTER = "/scratch/kpate385/legalft/saves/mistral7b-legal-qlora"

prompts = [
    "Who is the respondent in a land dispute appeal filed by a tenant against a landlord?",
    "What is the limitation period for a suit to recover possession of immovable property in India?",
    "In a partition suit, what is the difference between a preliminary decree and a final decree?",
    "What does adverse possession mean under Indian property law?",
]

bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                         bnb_4bit_compute_dtype=torch.bfloat16)
tok = AutoTokenizer.from_pretrained(BASE)

def gen(model, q):
    ids = tok.apply_chat_template([{"role":"user","content":q}],
                                  add_generation_prompt=True, return_tensors="pt", return_dict=False).to(model.device)
    out = model.generate(ids, max_new_tokens=200, do_sample=False)
    return tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True).strip()

print("loading base (4-bit)...")
base = AutoModelForCausalLM.from_pretrained(BASE, quantization_config=bnb, device_map="auto")
base_ans = [gen(base, p) for p in prompts]

print("attaching fine-tuned adapter...")
ft = PeftModel.from_pretrained(base, ADAPTER)
ft_ans = [gen(ft, p) for p in prompts]

for i, p in enumerate(prompts):
    print("\n" + "="*80)
    print("Q:", p)
    print("-"*80); print("BASE:      ", base_ans[i])
    print("-"*80); print("FINETUNED: ", ft_ans[i])
