#include <agent_memory.hpp>

#include <iostream>
#include <string>

int main() {
    using namespace agent_memory;

    CanonicalDocumentRevision document{
        DocumentId{"doc:sovetwave-example"},
        1,
        {},
        {
            {ContentBlockId{"role"}, 1, ContentBlockKind::Heading, std::nullopt,
             "You are a software assistant."},
            {ContentBlockId{"instruction"}, 1, ContentBlockKind::Paragraph,
             ContentBlockId{"role"}, "Use {{term.computer}} in the answer."},
            {ContentBlockId{"ru"}, 1, ContentBlockKind::Paragraph, std::nullopt,
             "Отвечай по-русски."},
            {ContentBlockId{"en"}, 1, ContentBlockKind::Paragraph, std::nullopt,
             "Answer in English."}
        }
    };

    PromptAssemblyProfile profile;
    profile.profile_id = "sovetwave-demo";
    profile.revision = 1;
    profile.selections = {
        {ContentBlockId{"instruction"}, true, true, true, true},
        {ContentBlockId{"ru"}, true, false, false, false, std::string{"ru"}},
        {ContentBlockId{"en"}, true, false, false, false, std::string{"en"}}
    };

    PromptRenderContext context;
    context.locale = "ru";
    context.style = "neutral";
    context.consumer_profile = "example";
    context.bindings.emplace("term.computer", PromptTemplateBinding{
        "term.computer", std::string{"компьютер"}, PromptBindingTrust::TrustedControl});

    const auto result = compile_prompt(document, profile, context);
    if(!result.succeeded()) {
        std::cerr << result.message << '\n';
        return 1;
    }
    std::cout << result.prompt->text << "\n"
              << "text=" << result.prompt->manifest.text_digest << "\n"
              << "config=" << result.prompt->manifest.configuration_digest << "\n";
    return 0;
}
